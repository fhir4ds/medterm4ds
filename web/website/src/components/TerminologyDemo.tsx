import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

/**
 * Live terminology search demo backed by the fhir4ds terminology-demo
 * HF Space (https://fhir4ds-terminology-demo.hf.space).
 *
 * Lets a visitor type a term, pick lexical/semantic/canonical/hybrid,
 * and see results, annotations and patient-friendly mappings.
 * Handles the Space's 48 h idle sleep with an explicit "waking" state.
 */

const SPACE_URL = 'https://fhir4ds-terminology-demo.hf.space';

const MODES = [
  {id: 'lexical', label: 'Lexical', hint: 'BM25 exact-term matching'},
  {id: 'semantic', label: 'Semantic', hint: 'SapBERT embedding similarity'},
  {id: 'canonical', label: 'Canonical', hint: 'anchored value-set cards'},
  {id: 'hybrid', label: 'Hybrid', hint: 'lexical + semantic combined'},
];

const STATUS_STYLES = {
  certain: 'badge badge--success',
  probable: 'badge--info badge',
  possible: 'badge badge--warning',
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

type State =
  | {phase: 'idle'}
  | {phase: 'loading'}
  | {phase: 'waking'; since: number}
  | {phase: 'error'; message: string}
  | {phase: 'done'};

function useRelativeWakeMessage(since: number): string {
  const [, force] = useState(0);
  useEffect(() => {
    const t = setInterval(() => force((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);
  const elapsed = Math.floor((Date.now() - since) / 1000);
  const left = Math.max(0, 60 - elapsed);
  return left > 0 ? `~${left}s` : 'almost there';
}

export default function TerminologyDemo(): JSX.Element {
  const [query, setQuery] = useState('myocardial infarction');
  const [mode, setMode] = useState('hybrid');
  const [text, setText] = useState(
    'Patient with type 2 diabetes on metformin. Family history of heart attack.',
  );
  const [state, setState] = useState<State>({phase: 'idle'});
  const [results, setResults] = useState<SearchResult[]>([]);
  const [concepts, setConcepts] = useState<Concept[]>([]);
  const [annotated, setAnnotated] = useState<string>('');
  const [friendly, setFriendly] = useState<Array<Record<string, unknown>>>([]);
  const abortRef = useRef<AbortController | null>(null);

  const runSearch = useCallback(async () => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setState({phase: 'loading'});
    setResults([]);
    setConcepts([]);
    setAnnotated('');
    setFriendly([]);
    const started = Date.now();
    try {
      const res = await fetch(`${SPACE_URL}/search`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({query, mode, count: 10}),
        signal: ac.signal,
      });
      if (res.status === 429) {
        setState({phase: 'error', message: 'Rate limit reached — wait a minute and try again.'});
        return;
      }
      if (!res.ok) throw new Error(`Space answered ${res.status}`);
      const data = await res.json();
      setResults(data.results ?? []);
      setState({phase: 'done'});
      void runExtract(started);
    } catch (err) {
      if (ac.signal.aborted) return;
      // Space asleep (network error / 503) — show the waking state.
      if (Date.now() - started < 15000) {
        setState({phase: 'waking', since: Date.now()});
        setTimeout(() => {
          if (!ac.signal.aborted) void runSearch();
        }, 15000);
      } else {
        setState({
          phase: 'error',
          message: err instanceof Error ? err.message : 'Request failed',
        });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, mode]);

  const runExtract = useCallback(async (searchStarted: number) => {
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

  const wakeLabel = useRelativeWakeMessage(
    state.phase === 'waking' ? (state as {since: number}).since : Date.now(),
  );
  const isWaking = state.phase === 'waking';

  const body = useMemo(() => {
    if (state.phase === 'loading') return <p>Searching…</p>;
    if (isWaking)
      return (
        <p>
          ⏳ <strong>Waking the Space</strong> — it sleeps after 48 h idle and
          can take up to ~60 s to wake ({wakeLabel}). This page will retry
          automatically.
        </p>
      );
    if (state.phase === 'error')
      return <p className="demo-error">⚠️ {state.message}</p>;
    if (state.phase === 'idle') return <p>Type a term and press Search.</p>;
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
  }, [state, results, isWaking, wakeLabel]);

  return (
    <div className="terminology-demo">
      <div className="terminology-demo__controls">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && runSearch()}
          placeholder="e.g. heart attack, HbA1c, amoxicillin"
          maxLength={200}
        />
        <select value={mode} onChange={(e) => setMode(e.target.value)}>
          {MODES.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label} — {m.hint}
            </option>
          ))}
        </select>
        <button
          className="button button--primary"
          onClick={() => void runSearch()}
          disabled={state.phase === 'loading' || isWaking}>
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
