import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';

/**
 * Live terminology search demo backed by the fhir4ds terminology-demo
 * HF Space (https://fhir4ds-terminology-demo.hf.space).
 *
 * Lets a visitor type a term, pick lexical/semantic/canonical/hybrid,
 * and see results, annotations and patient-friendly mappings.
 *
 * Startup gating: the form stays disabled until the Space is actually
 * usable. GET /health is polled every 2.5 s (5 s timeout per attempt —
 * the first request wakes a sleeping container and may hang, so
 * timeout-then-retry is the expected path, not an error). Once health
 * says ready, GET /capabilities is called once to confirm the DB/indexes
 * really loaded before the form is enabled. Any 503 is treated as
 * not-ready and re-enters the same polling loop.
 */

const SPACE_URL = 'https://fhir4ds-terminology-demo.hf.space';

const HEALTH_POLL_INTERVAL_MS = 2500;
const FETCH_TIMEOUT_MS = 5000;
const GIVE_UP_AFTER_MS = 150000; // ~2.5 min of consecutive failures

const MODES = [
  {id: 'lexical', label: 'Lexical', hint: 'BM25 exact-term matching'},
  {id: 'semantic', label: 'Semantic', hint: 'SapBERT embedding similarity'},
  {id: 'canonical', label: 'Canonical', hint: 'anchored value-set cards'},
  {id: 'hybrid', label: 'Hybrid', hint: 'lexical + semantic combined'},
];

const STATUS_STYLES = {
  certain: 'badge badge--success',
  probable: 'badge--info badge',
  possible: 'badge--warning',
};

type SearchResult = {
  code?: string;
  source?: string;
  display?: string;
  score?: number;
  match_grade?: keyof typeof STATUS_STYLES;
  category?: string;
  canonical_id?: string;
  patient_friendly_name?: string;
  members?: Array<{source?: string; code?: string; display?: string}>;
};

type Concept = {
  code?: string;
  source?: string;
  display?: string;
  matched_text?: string;
  status?: string;
  match_grade?: keyof typeof STATUS_STYLES;
  canonical_id?: string;
  span_start?: number;
  span_end?: number;
};

type StartupStatus = 'checking' | 'starting' | 'ready' | 'failed';

type SearchState =
  | {phase: 'idle'}
  | {phase: 'loading'}
  | {phase: 'error'; message: string}
  | {phase: 'done'};

function fetchWithTimeout(url: string, init?: RequestInit): Promise<Response> {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), FETCH_TIMEOUT_MS);
  return fetch(url, {...init, signal: ac.signal}).finally(() => clearTimeout(timer));
}

/**
 * Polls GET /health until the Space reports ready, then confirms once with
 * GET /capabilities (which touches the DB) before declaring ready.
 * /health itself is cheap by design and never loads models or the DB.
 */
function useSpaceStartup(): {
  status: StartupStatus;
  notReady: () => void;
  retry: () => void;
} {
  const [status, setStatus] = useState<StartupStatus>('checking');
  const [epoch, setEpoch] = useState(0);
  const startedAtRef = useRef(Date.now());
  const aliveRef = useRef(true);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);

  useEffect(() => {
    aliveRef.current = true;
    startedAtRef.current = Date.now();
    attemptRef.current = 0;
    setStatus('checking');

    const scheduleNext = (delay: number) => {
      timerRef.current = setTimeout(runAttempt, delay);
    };

    const runAttempt = async () => {
      if (!aliveRef.current) return;
      attemptRef.current += 1;
      try {
        const res = await fetchWithTimeout(`${SPACE_URL}/health`);
        if (!aliveRef.current) return;
        if (res.status === 503) throw new Error('starting');
        if (!res.ok) throw new Error(`health ${res.status}`);
        const data = (await res.json()) as {ready?: boolean};
        if (!data.ready) throw new Error('not ready yet');
        // Health is up — confirm the DB/indexes actually loaded.
        const caps = await fetchWithTimeout(`${SPACE_URL}/capabilities`);
        if (!aliveRef.current) return;
        if (caps.status === 503 || !caps.ok) throw new Error('capabilities not ready');
        setStatus('ready');
        return;
      } catch {
        if (!aliveRef.current) return;
        const elapsed = Date.now() - startedAtRef.current;
        if (elapsed >= GIVE_UP_AFTER_MS) {
          setStatus('failed');
          return;
        }
        setStatus(attemptRef.current <= 2 ? 'checking' : 'starting');
        scheduleNext(HEALTH_POLL_INTERVAL_MS);
      }
    };

    void runAttempt();
    return () => {
      aliveRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [epoch]);

  // Drop back to polling (e.g. a mid-session 503 / network drop).
  const notReady = useCallback(() => {
    setStatus((s) => (s === 'ready' ? 'checking' : s));
    setEpoch((n) => n + 1);
  }, []);

  const retry = useCallback(() => setEpoch((n) => n + 1), []);

  return {status, notReady, retry};
}

export default function TerminologyDemo(): JSX.Element {
  const [query, setQuery] = useState('myocardial infarction');
  const [mode, setMode] = useState('hybrid');
  const [text, setText] = useState(
    'Patient with type 2 diabetes on metformin. Family history of heart attack.',
  );
  const [state, setState] = useState<SearchState>({phase: 'idle'});
  const [results, setResults] = useState<SearchResult[]>([]);
  const [concepts, setConcepts] = useState<Concept[]>([]);
  const [annotated, setAnnotated] = useState<string>('');
  const [friendly, setFriendly] = useState<Array<Record<string, unknown>>>([]);
  const {status: startup, notReady, retry} = useSpaceStartup();
  const abortRef = useRef<AbortController | null>(null);

  const runSearch = useCallback(async () => {
    if (startup !== 'ready') return;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setState({phase: 'loading'});
    setResults([]);
    setConcepts([]);
    setAnnotated('');
    setFriendly([]);
    try {
      const res = await fetch(`${SPACE_URL}/search`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({query, mode, count: 10}),
        signal: ac.signal,
      });
      if (res.status === 503) {
        notReady();
        return;
      }
      if (res.status === 429) {
        setState({phase: 'error', message: 'Rate limit reached — wait a minute and try again.'});
        return;
      }
      if (!res.ok) throw new Error(`Space answered ${res.status}`);
      const data = await res.json();
      setResults(data.results ?? []);
      setState({phase: 'done'});
      void runExtract();
    } catch (err) {
      if (ac.signal.aborted) return;
      // Network-level failure: most likely the Space went back to sleep.
      notReady();
      setState({
        phase: 'error',
        message:
          'Lost contact with the demo Space — it may have gone back to sleep. ' +
          'Waiting for it to come back; the form will re-enable when it is ready.',
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, mode, startup, notReady]);

  const runExtract = useCallback(async () => {
    try {
      const res = await fetch(`${SPACE_URL}/extract`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text}),
      });
      if (!res.ok) return;
      const data = await res.json();
      setConcepts(data.concepts ?? []);
      setAnnotated(data.annotated_text ?? '');
      void runTranslate(data.concepts ?? []);
    } catch {
      // extraction is best-effort in the demo
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);

  const runTranslate = useCallback(async (found: Concept[]) => {
    const codes = found
      .filter((c) => c.source && c.code)
      .slice(0, 5)
      .map((c) => ({source: c.source, code: c.code}));
    if (!codes.length) return;
    try {
      const res = await fetch(`${SPACE_URL}/translate`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({codes}),
      });
      if (!res.ok) return;
      const data = await res.json();
      setFriendly(data.results ?? []);
    } catch {
      // best-effort
    }
  }, []);

  const startupBanner = useMemo(() => {
    if (startup === 'ready')
      return (
        <p className="demo-status demo-status--ready">
          ✅ <strong>Ready.</strong> Connected to the terminology demo Space.
        </p>
      );
    if (startup === 'checking')
      return <p className="demo-status">🔎 Checking the terminology server…</p>;
    if (startup === 'starting')
      return (
        <p className="demo-status">
          🌅 <strong>Starting up</strong> — the demo Space sleeps when idle;
          first start can take up to about 2 minutes.
        </p>
      );
    return (
      <div className="demo-status demo-status--error">
        <p>
          ⚠️ <strong>Could not reach the demo Space</strong> after several
          minutes of trying. It may still be waking up, or temporarily
          unavailable.
        </p>
        <button className="button button--secondary" onClick={retry}>
          Retry
        </button>
      </div>
    );
  }, [startup, retry]);

  const formDisabled = startup !== 'ready' || state.phase === 'loading';

  const body = useMemo(() => {
    if (state.phase === 'loading') return <p>Searching…</p>;
    if (state.phase === 'error')
      return <p className="demo-error">⚠️ {state.message}</p>;
    if (state.phase === 'idle')
      return <p>Type a term and press Search.</p>;
    if (!results.length) return <p>No results for this query.</p>;
    return (
      <table>
        <thead>
          <tr>
            <th>Display</th>
            <th>Code</th>
            <th>Source</th>
            <th>Category</th>
            <th>Score</th>
            <th>Grade</th>
          </tr>
        </thead>
        <tbody>
          {results.map((r, i) => (
            <tr key={i}>
              <td>{r.display ?? r.patient_friendly_name ?? r.canonical_id}</td>
              <td>
                <code>{r.code ?? r.canonical_id}</code>
              </td>
              <td>{r.source}</td>
              <td>{r.category}</td>
              <td>{r.score != null ? r.score.toFixed(3) : ''}</td>
              <td>
                {r.match_grade ? (
                  <span className={STATUS_STYLES[r.match_grade] ?? 'badge'}>
                    {r.match_grade}
                  </span>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }, [state, results]);

  return (
    <div className="terminology-demo">
      {startupBanner}

      <div className="terminology-demo__controls">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && runSearch()}
          placeholder="e.g. heart attack, HbA1c, amoxicillin"
          maxLength={200}
          disabled={formDisabled}
        />
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value)}
          disabled={formDisabled}>
          {MODES.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label} — {m.hint}
            </option>
          ))}
        </select>
        <button
          className="button button--primary"
          onClick={() => void runSearch()}
          disabled={formDisabled}>
          Search
        </button>
      </div>

      <h4>Results</h4>
      {body}

      <h4>Clinical-note extraction &amp; annotations</h4>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        maxLength={4000}
        disabled={formDisabled}
      />
      {concepts.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Matched text</th>
              <th>Concept</th>
              <th>Code</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {concepts.slice(0, 12).map((c, i) => (
              <tr key={i}>
                <td>{c.matched_text}</td>
                <td>{c.display}</td>
                <td>
                  <code>
                    {c.source}/{c.code}
                  </code>
                </td>
                <td>{c.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {annotated && (
        <p className="terminology-demo__annotated">
          <strong>Annotated:</strong> {annotated}
        </p>
      )}

      {friendly.length > 0 && (
        <>
          <h4>Patient-friendly translations</h4>
          <table>
            <thead>
              <tr>
                <th>Code</th>
                <th>Patient-friendly name</th>
                <th>Match type</th>
              </tr>
            </thead>
            <tbody>
              {friendly.map((f, i) => (
                <tr key={i}>
                  <td>
                    <code>
                      {String((f as {source?: string}).source ?? '')}/
                      {String((f as {code?: string}).code ?? '')}
                    </code>
                  </td>
                  <td>{String((f as {name?: string}).name ?? '')}</td>
                  <td>{String((f as {match_type?: string}).match_type ?? '')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
