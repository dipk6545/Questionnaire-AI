import { useEffect, useRef, useState } from 'react';
import api from './services/api';
import './App.css';

const DETAILS = {
  SQL: ['{ }', 'Queries, transactions & data modeling'], Python: ['Py', 'Code, concurrency & problem solving'],
  Statistics: ['σ', 'Inference, experiments & uncertainty'], 'Data Science': ['DS', 'Data quality, analysis & decisions'],
  'Machine Learning': ['ML', 'Models, validation & generalization'], 'Deep Learning': ['DL', 'Networks, optimization & inference'],
  MLOps: ['Ops', 'Deployment, monitoring & reliability'], 'AI Engineering': ['AI', 'LLMs, retrieval & evaluation'],
};
const LEVEL_COPY = ['Applied foundations', 'Connected concepts', 'Multi-step reasoning', 'Incidents & real-world tradeoffs'];
const DEFAULT_OPTIONS = {
  subjects: Object.keys(DETAILS),
  levels: ['Easy', 'Medium', 'Hard', 'Production grade'],
  question_count: 5
};
const message = (e) => typeof e.response?.data?.detail === 'string' ? e.response.data.detail
  : e.code === 'ECONNABORTED' ? 'This is taking longer than expected. Check History before starting again.'
    : e.message || 'Unable to complete this request.';
const date = (value) => new Date(value).toLocaleString();

export default function App() {
  const [user, setUser] = useState(null);
  const [view, setView] = useState('login');
  const [busy, setBusy] = useState('session');
  const [error, setError] = useState('');
  const [options, setOptions] = useState(DEFAULT_OPTIONS);
  const [subject, setSubject] = useState('');
  const [level, setLevel] = useState('');
  const [exam, setExam] = useState(null);
  const [answers, setAnswers] = useState({});
  const [result, setResult] = useState(null);
  const [history, setHistory] = useState({ attempts: [], performance: [] });
  const [saveStatus, setSaveStatus] = useState('');
  const [showSubmitModal, setShowSubmitModal] = useState(false);
  const initialized = useRef(false);
  const saveQueue = useRef(Promise.resolve());
  const revision = useRef(0);
  const dirty = useRef(new Map());
  const heading = useRef(null);
  const errorBanner = useRef(null);

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    const initialize = async () => {
      const params = new URLSearchParams(window.location.search);
      try {
        if (params.has('error')) throw new Error('Google sign-in was cancelled. Please try again.');
        if (params.has('code')) {
          const state = params.get('state');
          if (!state || state !== sessionStorage.getItem('oauth-state')) throw new Error('Invalid sign-in request. Please try again.');
          const { data } = await api.get('/auth/authenticate', { params: { code: params.get('code'), state } });
          sessionStorage.setItem('questionnaire-token', data.token);
          setUser(data); setView('setup');
        } else if (sessionStorage.getItem('questionnaire-token')) {
          const { data } = await api.get('/auth/me');
          setUser(data); setView('setup');
        }
      } catch (e) { sessionStorage.removeItem('questionnaire-token'); setError(message(e)); }
      finally {
        if (params.has('code') || params.has('error')) {
          window.history.replaceState({}, '', window.location.pathname);
          sessionStorage.removeItem('oauth-state');
        }
        setBusy('');
      }
    };
    initialize();
  }, []);

  useEffect(() => {
    const id = api.interceptors.response.use(r => r, e => {
      if (e.response?.status === 401) {
        sessionStorage.removeItem('questionnaire-token');
        setUser(null); setExam(null); setResult(null); setView('login');
        setError('Your session expired. Sign in again; saved assessments remain in your history.');
      }
      return Promise.reject(e);
    });
    return () => api.interceptors.response.eject(id);
  }, []);

  const triggerSeed = () => {
    api.post("/examinations/seed-tick").catch(() => {});
  };

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    Promise.all([api.get('/examinations/options'), api.get('/examinations/history')])
      .then(([o, h]) => { if (!cancelled) { setOptions(o.data); setHistory(h.data); } })
      .catch(e => { if (!cancelled) setError(message(e)); });
    return () => { cancelled = true; };
  }, [user]);

  useEffect(() => { heading.current?.focus(); window.scrollTo(0, 0); }, [view]);
  useEffect(() => {
    if (error) {
      errorBanner.current?.focus({ preventScroll: true });
      errorBanner.current?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }, [error]);
  useEffect(() => {
    if (view !== 'quiz') return;
    const warn = e => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [view]);

  async function action(name, work) {
    setBusy(name); setError('');
    try { await work(); } catch (e) { setError(message(e)); } finally { setBusy(''); }
  }

  function login() {
    action('login', async () => {
      const { data } = await api.get('/auth/login');
      sessionStorage.setItem('oauth-state', data.state);
      window.location.assign(data.url);
    });
  }

  function signOut() {
    sessionStorage.removeItem('questionnaire-token');
    setUser(null);
    setView('login');
    setExam(null);
    setResult(null);
    setOptions(null);
    setSubject('');
    setLevel('');
    setHistory({ attempts: [], performance: [] });
    api.post('/auth/logout').catch(() => {});
  }

  function showExam(data) {
    dirty.current.clear();
    setExam(data); setAnswers(Object.fromEntries(data.questions.map(q => [q.id, q.selected_options])));
    setSaveStatus('Saved to your account'); setView('quiz');
  }

  function start() {
    action('generate', async () => {
      const { data } = await api.post('/examinations', { subject, level, request_id: crypto.randomUUID() });
      showExam(data);
    });
  }

  function choose(q, index) {
    triggerSeed();
    const selected = q.kind === 'single' ? [index]
      : (answers[q.id] || []).includes(index) ? answers[q.id].filter(i => i !== index) : [...(answers[q.id] || []), index];
    setAnswers(old => ({ ...old, [q.id]: selected }));
    const currentRevision = ++revision.current;
    dirty.current.set(q.id, currentRevision);
    setSaveStatus('Saving…');
    // Serial requests prevent an older selection overwriting a newer one.
    saveQueue.current = saveQueue.current.then(() => api.put(`/examinations/${exam.id}/answers`, {
      answers: [{ question_id: q.id, selected_options: selected }],
    })).then(() => {
      if (dirty.current.get(q.id) === currentRevision) dirty.current.delete(q.id);
      if (revision.current === currentRevision) setSaveStatus(dirty.current.size ? 'Some answers are unsaved. Use Save all before leaving.' : 'Saved to your account');
    }).catch(() => { if (revision.current === currentRevision) setSaveStatus('Some answers are unsaved. Submit or retry saving before leaving.'); });
  }

  const batch = () => ({ answers: exam.questions.map(q => ({ question_id: q.id, selected_options: answers[q.id] || [] })) });
  function saveAll() {
    action('save', async () => {
      await saveQueue.current; await api.put(`/examinations/${exam.id}/answers`, batch());
      dirty.current.clear();
      setSaveStatus('Saved to your account');
    });
  }
  function submit() {
    setShowSubmitModal(true);
  }

  function confirmSubmit() {
    setShowSubmitModal(false);
    action('submit', async () => {
      const { data } = await api.post(`/examinations/${exam.id}/submit`, batch());
      setResult(data); setView('result');
      if (!data.feedback) {
        let attempts = 0;
        const interval = setInterval(async () => {
          attempts++;
          try {
            const res = await api.get(`/examinations/${data.id}/result`);
            if (res.data?.feedback) {
              setResult(res.data);
              clearInterval(interval);
              triggerSeed();
            } else if (attempts >= 8) {
              clearInterval(interval);
            }
          } catch (_) {
            if (attempts >= 8) clearInterval(interval);
          }
        }, 1200);
      }
    });
  }

  function showHistory() {
    action('history', async () => {
      await saveQueue.current;
      if (view === 'quiz') await api.put(`/examinations/${exam.id}/answers`, batch());
      const { data } = await api.get('/examinations/history'); setHistory(data); setView('history');
    });
  }
  function openAttempt(attempt) {
    action('open', async () => {
      if (attempt.status === 'submitted') {
        const { data } = await api.get(`/examinations/${attempt.id}/result`); setResult(data); setView('result');
      } else {
        const { data } = await api.get(`/examinations/${attempt.id}`); showExam(data);
      }
    });
  }
  function discard(attempt) {
    if (!window.confirm('Discard this unfinished assessment? It will remain listed in your history.')) return;
    action('discard', async () => {
      await api.post(`/examinations/${attempt.id}/discard`);
      const { data } = await api.get('/examinations/history'); setHistory(data);
    });
  }
  function feedback() {
    action('feedback', async () => {
      const { data } = await api.post(`/examinations/${result.id}/feedback`); setResult(data);
    });
  }
  const answered = exam?.questions.filter(q => answers[q.id]?.length).length || 0;
  const disabled = !!busy;

  return <div className={"app-shell " + (view === "login" ? "login-shell" : "")}>
    {view !== "login" && <header className="topbar"><div className="topbar-inner"><a href="/" className="brand"><span className="brand-icon">Q</span><span>Questionnaire <b>AI</b><small>APPLIED SKILLS ASSESSMENTS</small></span></a>
      {user && <div className="account"><span className="avatar">{(user.name || user.email)[0].toUpperCase()}</span><span className="account-name">{user.name || user.email}</span><button className="text-button" onClick={signOut} disabled={disabled}>Sign out</button></div>}
    </div></header>}
    <main className={"workspace " + (view === "login" ? "login-workspace" : "")}>
      {user && <nav className="navigation" aria-label="Main"><button className={view === 'setup' ? 'active' : ''} disabled={disabled || view === 'quiz'} onClick={() => { setView('setup'); setError(''); }}>New assessment</button><button className={view === 'history' ? 'active' : ''} disabled={disabled} onClick={showHistory}>History & performance</button></nav>}
      {error && (
        <div className="toast-error" ref={errorBanner} tabIndex={-1} role="alert">
          <div className="toast-content">
            <span className="toast-icon">⚠️</span>
            <div>
              <strong>Error</strong>
              <p>{error}</p>
            </div>
          </div>
          <button className="toast-close" onClick={() => setError('')} aria-label="Dismiss error">×</button>
        </div>
      )}

      {view === 'login' && <section className="login-view"><div className="eyebrow">PRACTICE WITH PURPOSE</div><h1 ref={heading} tabIndex={-1}>Turn knowledge<br />into confidence.</h1><p className="lead">Realistic questions. Clear feedback.<br />A better picture of your professional skills.</p><div className="login-card"><span className="brand-icon">Q</span><h2>Your next challenge starts here</h2><p>Sign in to take assessments and track your progress.</p><button className="google-button" onClick={login} disabled={disabled}><span className="google-mark" aria-hidden="true">G</span>{busy ? 'Connecting…' : 'Continue with Google'}</button><small>Your assessments and results are saved to your account.</small></div><div className="login-facts"><span>8 subjects</span><span>4 levels</span><span>5 questions per assessment</span></div></section>}

      {view === 'setup' && <section><div className="eyebrow">BUILD YOUR PRACTICE</div><h1 ref={heading} tabIndex={-1}>Choose your challenge.</h1><p className="lead">Focus on a subject. Test your judgment. Learn what to work on next.</p>
        <div className="section-label"><h2><span>01</span> Pick a subject</h2><small>Choose one</small></div>
        <div className="subject-grid">{options ? options.subjects.map((s, i) => <button key={s} disabled={disabled} aria-pressed={subject === s} className={`subject-card ${subject === s ? 'selected' : ''}`} onClick={() => { setSubject(s); triggerSeed(); }}><span className={`subject-icon tone-${i % 4}`}>{DETAILS[s][0]}</span><strong>{s}</strong><small>{DETAILS[s][1]}</small><span className="choice-indicator" aria-hidden="true">{subject === s ? '✓' : '+'}</span></button>) : <p role="status">Loading subjects… <button className="text-button" onClick={() => action('options', async () => { const { data } = await api.get('/examinations/options'); setOptions(data); })}>Retry</button></p>}</div>
        <div className="section-label"><h2><span>02</span> Set the level</h2><small>Choose one</small></div><div className="level-grid">{options?.levels.map((l, i) => <label key={l} className={`level-card ${level === l ? 'selected' : ''}`}><input type="radio" name="level" checked={level === l} onChange={() => { setLevel(l); triggerSeed(); }} disabled={disabled} /><span><strong>{l}</strong><small>{LEVEL_COPY[i]}</small></span></label>)}</div>
        {level === 'Production grade' && <p className="context-note">Expect realistic debugging, deployment and reliability scenarios, with constraints and tradeoffs grounded in {subject || 'your selected subject'}.</p>}
        <div className="rules"><strong>A short test. A meaningful challenge.</strong><p>5 questions · 4 single-answer + 1 multiple-answer · Untimed</p><p>1 mark per correct question. Multiple-answer questions require all correct options and no incorrect options. No partial credit or negative marking.</p></div>
        <div className="action-bar"><div><strong>{subject || 'Select a subject'}{level && ` · ${level}`}</strong><small>Fresh questions, reviewed before you begin</small></div><button className="primary" disabled={!subject || !level || disabled} onClick={start}>{busy === 'generate' ? 'Loading assessment…' : 'Start assessment →'}</button></div>{busy === 'generate' && <p className="status-note" role="status">Fetching questions from your question bank...</p>}
      </section>}

      {view === 'quiz' && exam && <section><div className="eyebrow">ASSESSMENT IN PROGRESS</div><h1 ref={heading} tabIndex={-1}>{exam.subject}</h1><div className="quiz-meta"><span>{exam.level} · 5 questions</span><strong aria-live="polite">{answered} of 5 answered</strong></div><progress aria-label="Questions answered" max={5} value={answered} /><p className="save-status" role="status">{saveStatus} <button className="text-button" disabled={disabled} onClick={saveAll}>Save all</button></p>
        <div className="questions">{exam.questions.map((q, i) => <fieldset className="question-card" key={q.id} disabled={disabled}><legend><span className="question-count">QUESTION {String(i + 1).padStart(2, '0')}</span><span className="question-text">{q.text}</span></legend><div className="question-meta"><span>{q.topic}</span><span className={q.kind === 'multiple' ? 'multiple-badge' : ''}>{q.kind === 'multiple' ? 'Select all correct options' : 'Select one option'}</span></div><div className="option-list">{q.options.map((o, j) => <label key={j} className={`answer-option ${answers[q.id]?.includes(j) ? 'chosen' : ''}`}><input type={q.kind === 'multiple' ? 'checkbox' : 'radio'} name={`q-${q.id}`} checked={answers[q.id]?.includes(j) || false} onChange={() => choose(q, j)} /><span className="option-letter">{'ABCD'[j]}</span><span>{o}</span></label>)}</div></fieldset>)}</div>
        <div className="action-bar"><div><strong>{answered === 5 ? 'Ready to review your performance?' : `${5 - answered} questions still to answer`}</strong><small>Your selections are final after submission.</small></div><button className="primary" disabled={answered !== 5 || disabled} onClick={submit}>{busy === 'submit' ? 'Scoring & preparing feedback…' : 'Submit assessment'}</button></div>
      </section>}

      {view === 'result' && result && <section><div className="eyebrow">ASSESSMENT COMPLETE</div><h1 ref={heading} tabIndex={-1}>Your results.</h1><p className="lead">{result.subject} · {result.level}</p><div className="result-card"><div className="score"><small>YOUR SCORE</small><strong>{result.score}<span> / {result.total}</span></strong><span>{result.percentage}% correct</span></div><div className="result-intro"><h2>Every attempt gives you direction.</h2>{result.feedback?.summary ? <p>{result.feedback.summary}</p> : <div className="generating-feedback-box"><span>Analyzing your performance and generating insights</span><div className="jumping-dots"><span /><span /><span /></div></div>}{!result.feedback && busy === 'feedback' && <div className="generating-feedback-box"><span>Generating feedback…</span><div className="jumping-dots"><span /><span /><span /></div></div>}</div></div>
        {result.feedback && <div className="feedback-grid">{[['Strengths', 'strengths'], ['Focus areas', 'focus_areas'], ['Next steps', 'next_steps']].map(([label, key]) => <section key={key} className="feedback-card"><h2>{label}</h2>{result.feedback[key].length ? <ul>{result.feedback[key].map((s, i) => <li key={i}>{s}</li>)}</ul> : <p>{key === 'strengths' ? 'Keep practicing to build more evidence.' : 'No specific gaps identified in this short assessment.'}</p>}</section>)}</div>}
        <div className="section-label"><h2>Answer review</h2><small>{result.score} correct · {5 - result.score} to revisit</small></div><div className="questions">{result.review.map((q, i) => <article className="review-card" key={q.id}><div className="review-header"><span className={`outcome ${q.correct ? 'correct' : 'incorrect'}`}>{q.correct ? 'Correct' : 'To revisit'}</span><small>Question {i + 1} · {q.topic} · {q.kind === 'multiple' ? 'Multiple answers' : 'Single answer'}</small></div><h3>{q.text}</h3><div className="option-list">{q.options.map((o, j) => <div key={j} className={`answer-option review-option ${q.correct_options.includes(j) ? 'right-answer' : q.selected_options.includes(j) ? 'wrong-answer' : ''}`}><span className="option-letter">{'ABCD'[j]}</span><span>{o}</span><small>{[q.correct_options.includes(j) && 'Correct option', q.selected_options.includes(j) && 'Your selection'].filter(Boolean).join(' · ')}</small></div>)}</div><p className="explanation">{q.explanation}</p></article>)}</div><div className="action-bar"><p>Results saved to your performance history.</p><button className="primary" disabled={disabled} onClick={() => { setView('setup'); setSubject(''); setLevel(''); }}>Take another assessment →</button></div>
      </section>}

      {view === 'history' && <section><div className="eyebrow">YOUR LEARNING RECORD</div><h1 ref={heading} tabIndex={-1}>History & performance.</h1><p className="lead">Resume unfinished work or see how your results change over time.</p>
        <div className="section-label"><h2>Performance by subject and level</h2></div>{history.performance.length ? <div className="performance-grid">{history.performance.map(p => <article className="performance-card" key={`${p.subject}-${p.level}`}><small>{p.level}</small><h3>{p.subject}</h3><strong>{p.average}% <span>average</span></strong><p>{p.attempts} completed · Best {p.best}%</p></article>)}</div> : <div className="empty-state">Complete your first assessment to start tracking your performance.</div>}
        <div className="section-label"><h2>Recent assessments</h2><button className="text-button" disabled={disabled} onClick={showHistory}>Refresh</button></div><p className="muted">Your latest 50 attempts. Performance summaries include all completed attempts.</p><div className="history-list">{history.attempts.length ? history.attempts.map(a => <article className="history-row" key={a.id}><div><strong>{a.subject}</strong><p>{a.level} · {date(a.created_at)}</p></div><span className="history-status">{a.percentage !== null ? `${a.percentage}%` : a.status.replaceAll('_', ' ')}</span><div className="history-actions">{['submitted', 'in_progress'].includes(a.status) && <button className="secondary" disabled={disabled} onClick={() => openAttempt(a)}>{a.status === 'submitted' ? 'View results' : 'Resume'}</button>}{['in_progress', 'generating', 'failed'].includes(a.status) && <button className="text-button" disabled={disabled} onClick={() => discard(a)}>Discard</button>}</div></article>) : <div className="empty-state">No assessments yet. Choose a subject to begin.</div>}</div>
      </section>}
      {showSubmitModal && (
        <div className="modal-overlay" onClick={() => setShowSubmitModal(false)}>
          <div className="modal-dialog" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <span className="modal-icon">✓</span>
              <h3>Submit Assessment?</h3>
            </div>
            <div className="modal-body">
              <p>Your answers will be evaluated and saved to your performance record. Multiple-answer questions require an exact match for full credit.</p>
            </div>
            <div className="modal-actions">
              <button className="secondary" onClick={() => setShowSubmitModal(false)}>Review answers</button>
              <button className="primary" onClick={confirmSubmit} disabled={disabled}>Yes, submit now</button>
            </div>
          </div>
        </div>
      )}
    </main>{view !== "login" && <footer className="footer">Questionnaire AI <span>Learn with context. Practice with purpose.</span></footer>}
  </div>;
}
