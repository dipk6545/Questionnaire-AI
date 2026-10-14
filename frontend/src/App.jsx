import { useState, useEffect } from 'react';
import api from './services/api';

export default function App() {
  const [loading, setLoading] = useState(false);
  const [user, setUser] = useState(null);
  const [error, setError] = useState(null);

  // Check URL parameters for OAuth callback code
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');

    if (code) {
      setLoading(true);
      // Clean query params from URL
      window.history.replaceState({}, document.title, window.location.pathname);

      api
        .get('/auth/authenticate', { params: { code } })
        .then((res) => {
          setUser(res.data);
          setLoading(false);
        })
        .catch((err) => {
          const detail = err.response?.data?.detail || err.message || 'Authentication failed';
          setError(detail);
          setLoading(false);
        });
    }
  }, []);

  const handleGoogleLogin = async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await api.get('/auth/login');
      if (res.data?.url) {
        window.location.href = res.data.url;
      }
    } catch (err) {
      const detail = err.response?.data?.detail || err.message || 'Failed to retrieve login URL';
      setError(detail);
      setLoading(false);
    }
  };

  const handleSignOut = () => {
    setUser(null);
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col items-center justify-center p-4">
      <div className="w-full max-w-md bg-slate-900 border border-slate-800 rounded-2xl p-8 shadow-2xl flex flex-col items-center">
        
        {/* App Header */}
        <div className="mb-8 text-center">
          <div className="w-14 h-14 bg-indigo-600/20 text-indigo-400 border border-indigo-500/30 rounded-2xl flex items-center justify-center mx-auto mb-4 text-2xl font-bold shadow-inner">
            AI
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-white">Questionnaire AI</h1>
          <p className="text-sm text-slate-400 mt-1">Smart Interview & Assessment Platform</p>
        </div>

        {error && (
          <div className="w-full mb-6 p-3 bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs rounded-xl text-center">
            {error}
          </div>
        )}

        {user ? (
          /* Logged In View */
          <div className="w-full flex flex-col items-center text-center space-y-4">
            <div className="w-16 h-16 rounded-full bg-emerald-500/20 border border-emerald-500/40 text-emerald-400 flex items-center justify-center text-xl font-semibold">
              {user.name ? user.name[0].toUpperCase() : 'U'}
            </div>
            <div>
              <h2 className="text-lg font-semibold text-white">{user.name || 'User'}</h2>
              <p className="text-sm text-slate-400">{user.email}</p>
            </div>
            <div className="w-full pt-4">
              <button
                onClick={handleSignOut}
                className="w-full py-2.5 px-4 rounded-xl border border-slate-700 bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm font-medium transition cursor-pointer"
              >
                Sign Out
              </button>
            </div>
          </div>
        ) : (
          /* Sign In with Google View */
          <div className="w-full flex flex-col items-center">
            <button
              onClick={handleGoogleLogin}
              disabled={loading}
              className="w-full flex items-center justify-center gap-3 py-3 px-4 rounded-xl bg-white hover:bg-slate-100 text-slate-900 font-medium text-sm transition shadow-lg disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
            >
              {loading ? (
                <span>Connecting...</span>
              ) : (
                <>
                  <svg className="w-5 h-5" viewBox="0 0 24 24">
                    <path
                      fill="#4285F4"
                      d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                    />
                    <path
                      fill="#34A853"
                      d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                    />
                    <path
                      fill="#FBBC05"
                      d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"
                    />
                    <path
                      fill="#EA4335"
                      d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"
                    />
                  </svg>
                  <span>Sign in with Google</span>
                </>
              )}
            </button>
          </div>
        )}

      </div>
    </div>
  );
}
