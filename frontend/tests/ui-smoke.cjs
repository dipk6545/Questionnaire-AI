// Offline UI smoke test. Requires Playwright; all backend calls use fixtures.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const out = process.env.UI_ARTIFACT_DIR || path.join(__dirname, '../../artifacts/ui');
    fs.mkdirSync(out, { recursive: true });
    const subjects = ['SQL', 'Python', 'Statistics', 'Data Science', 'Machine Learning', 'Deep Learning', 'MLOps', 'AI Engineering'];
    const questions = Array.from({ length: 5 }, (_, i) => ({
      id: i + 1, text: i === 4
        ? 'A PostgreSQL deployment must reduce query latency without compromising writes. Which checks should you perform before adding an index? Select all correct options.'
        : `A reporting query slows after a deployment. The EXPLAIN plan shows a sequential scan on a large orders table. Which next step best tests whether an index will help? (Scenario ${i + 1})`,
      topic: 'Query plans and operational tradeoffs', kind: i === 4 ? 'multiple' : 'single',
      options: ['Compare EXPLAIN ANALYZE with representative data and predicates.', 'Measure write overhead and storage on a staging workload.',
        'Add an index to every column immediately.', 'Disable query monitoring to reduce overhead.'], selected_options: [],
    }));
    let exam = { id: 'fixture', subject: 'SQL', level: 'Production grade', status: 'in_progress', questions };
    let generationRequests = 0;
    const result = { id: 'fixture', subject: 'SQL', level: 'Production grade', score: 5, total: 5, percentage: 100,
      feedback_status: 'ready', feedback: { summary: 'You identified useful diagnostic steps and considered the cost of indexes. This short assessment shows promising operational reasoning.',
        strengths: ['Evidence-based query diagnosis'], focus_areas: [], next_steps: ['Compare execution plans before and after an index change.'] },
      review: questions.map(q => ({ ...q, correct_options: q.kind === 'multiple' ? [0, 1] : [0], selected_options: q.kind === 'multiple' ? [0, 1] : [0],
        correct: true, explanation: 'Measure the actual query plan and workload first. Additional indexes can improve reads while increasing write and storage costs.' })) };
    await page.route('**/api/v1/**', async route => {
      const url = new URL(route.request().url());
      const method = route.request().method();
      let data;
      if (url.pathname.endsWith('/auth/me')) data = { id: 1, name: 'Test Student', email: 'test@example.test' };
      else if (url.pathname.endsWith('/options')) data = { subjects, levels: ['Easy', 'Medium', 'Hard', 'Production grade'] };
      else if (url.pathname.endsWith('/history')) data = {
        attempts: [{ id: 'fixture', subject: 'SQL', level: 'Production grade', status: 'submitted', percentage: 100, created_at: '2026-10-16T10:00:00Z' }],
        performance: [{ subject: 'SQL', level: 'Production grade', attempts: 1, average: 100, best: 100 }] };
      else if (url.pathname.endsWith('/examinations') && method === 'POST') {
        if (generationRequests++ === 0) {
          await route.fulfill({ status: 502, json: { detail: 'Generated questions failed validation. Please retry.' } });
          return;
        }
        data = exam;
      }
      else if (url.pathname.endsWith('/answers')) data = { saved: true };
      else if (url.pathname.endsWith('/submit') || url.pathname.endsWith('/result')) data = result;
      else if (url.pathname.endsWith('/fixture')) data = exam;
      else throw new Error(`Unexpected UI request: ${method} ${url.pathname}`);
      await route.fulfill({ json: data });
    });
    const base = process.env.UI_BASE_URL || 'http://127.0.0.1:8502';
    await page.goto(base);
    await page.getByRole('button', { name: 'Continue with Google' }).waitFor();
    await page.screenshot({ path: path.join(out, 'login.png'), fullPage: true });
    await page.evaluate(() => sessionStorage.setItem('questionnaire-token', 'fixture'));
    await page.reload();
    await page.getByRole('button', { name: /Queries, transactions/ }).click();
    await page.getByRole('radio', { name: /Production grade/ }).check();
    await page.screenshot({ path: path.join(out, 'setup-desktop.png'), fullPage: true });
    await page.getByRole('button', { name: 'Start assessment' }).click();
    await page.getByRole('alert').waitFor();
    await page.waitForFunction(() => {
      const banner = document.querySelector('[role="alert"]');
      const rect = banner.getBoundingClientRect();
      return document.activeElement === banner && rect.top >= 0 && rect.bottom <= innerHeight;
    });
    await page.getByRole('button', { name: 'Dismiss error' }).click();
    await page.getByRole('button', { name: 'Start assessment' }).click();
    await page.getByRole('heading', { name: 'SQL', exact: true }).waitFor();
    assert.equal(await page.getByRole('radio').count(), 16);
    assert.equal(await page.getByRole('checkbox').count(), 4);
    const groups = page.locator('fieldset');
    for (let i = 0; i < 4; i++) await groups.nth(i).getByRole('radio').first().check();
    await groups.nth(4).getByRole('checkbox').nth(0).check();
    await groups.nth(4).getByRole('checkbox').nth(1).check();
    await page.getByText('5 of 5 answered', { exact: true }).waitFor();
    await page.screenshot({ path: path.join(out, 'quiz-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await page.screenshot({ path: path.join(out, 'quiz-mobile.png'), fullPage: true });
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: 'Submit assessment' }).click();
    await page.getByRole('heading', { name: 'Your results.' }).waitFor();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await page.screenshot({ path: path.join(out, 'result-mobile.png'), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1050 });
    await page.screenshot({ path: path.join(out, 'result-desktop.png'), fullPage: true });
    await page.getByRole('button', { name: 'History & performance' }).click();
    await page.getByRole('button', { name: 'View results' }).waitFor();
    await page.screenshot({ path: path.join(out, 'history-desktop.png'), fullPage: true });
    assert.deepEqual(errors, []);
    console.log('UI smoke passed: login, setup, radio/checkbox mix, saved answers, submission, results, history, mobile overflow.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
