import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {
  FhirParam,
  fhirParams,
  lookupSummary,
  searchRows,
  SearchRow,
  sourceShortName,
  translateMatches,
} from './terminologyFhir';

/**
 * Live terminology demo parts backed by the fhir4ds terminology-demo-server
 * HF Space (https://fhir4ds-terminology-demo-server.hf.space), which serves
 * the REAL medterm4ds FHIR R4 terminology API. Split into two components so
 * the docs page can place each inside its own subsection:
 *
 *  - <TerminologyDemoSearch /> (### Search): startup banner, the query
 *    input, and POST /fhir/CodeSystem/$search for lexical, semantic and
 *    canonical at once — top 3 per mode card, best result highlighted.
 *    Ranking (score + match-grade extension) lives on each FHIR Bundle
 *    ENTRY's `search` element; canonical entries' display IS the
 *    patient-friendly name.
 *  - <TerminologyDemoAnnotations /> (### Annotations): the note textarea
 *    and POST /fhir/CodeSystem/$extract — FHIR Bundle entries carry
 *    matched_text, span_start/end, status and canonical_id; rendered as an
 *    annotation viewer (status-tinted inline highlights + resolved concept,
 *    patient-friendly name, code), plus $lookup/$translate detail for the
 *    first concept.
 *
 * Both gate on the same readiness protocol: GET /health is polled every
 * 2.5 s (5 s timeout per attempt — the first request wakes a sleeping
 * container and may hang, so timeout-then-retry is the expected path, not
 * an error) until search_ready (models loaded), confirmed by
 * GET /capabilities. Any 503 re-enters the same polling loop. /health is
 * rate-limit exempt, so the two independent pollers are free.
 */

const SPACE_URL = 'https://fhir4ds-terminology-demo-server.hf.space';

const HEALTH_POLL_INTERVAL_MS = 2500;
const FETCH_TIMEOUT_MS = 5000;
const GIVE_UP_AFTER_MS = 150000; // ~2.5 min of consecutive failures
const COMPARE_TOP = 3; // results per search mode card
const MAX_TERM_DETAILS = 4; // concepts enriched with patient-friendly names

const SEARCH_MODES = [
  {id: 'lexical', label: 'Lexical', hint: 'exact words'},
  {id: 'semantic', label: 'Semantic', hint: 'meaning'},
  {id: 'canonical', label: 'Canonical', hint: 'grouped codes'},
] as const;

const GRADE_STYLES: Record<string, string> = {
  certain: 'terminology-demo__grade terminology-demo__grade--certain',
  exact: 'terminology-demo__grade terminology-demo__grade--certain',
  probable: 'terminology-demo__grade terminology-demo__grade--probable',
  possible: 'terminology-demo__grade terminology-demo__grade--possible',
};

type ExtractConcept = {
  code?: string;
  source?: string;
  system?: string;
  display?: string;
  matched_text?: string;
  status?: string;
  ner_label?: string;
  result_type?: string;
  canonical_id?: string;
  span_start?: number;
  span_end?: number;
};

type TermDetail = {concept: ExtractConcept; patientFriendly?: string};

type StartupStatus = 'checking' | 'starting' | 'ready' | 'failed';

type SearchState =
  | {phase: 'idle'}
  | {phase: 'loading'}
  | {phase: 'error'; message: string}
  | {phase: 'done'};

type ExtractState = {phase: 'idle' | 'loading'} | {phase: 'error'; message: string};

function fetchWithTimeout(url: string, init?: RequestInit): Promise<Response> {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), FETCH_TIMEOUT_MS);
  return fetch(url, {...init, signal: ac.signal}).finally(() => clearTimeout(timer));
}

async function postSearch(query: string, mode: string, count: number): Promise<SearchRow[]> {
  const res = await fetch(`${SPACE_URL}/fhir/CodeSystem/$search`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: fhirParams([
      ['query', query],
      ['searchMode', mode],
      ['count', count],
    ]),
  });
  if (!res.ok) return [];
  return searchRows(await res.json());
}

/**
 * Polls GET /health until the Space reports search_ready (models loaded),
 * then confirms once with GET /capabilities (which touches the DB) before
 * declaring ready. /health itself is cheap and never loads models or the DB.
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
        const data = (await res.json()) as {search_ready?: boolean};
        // Gate on search_ready (models loaded), not just db_ready — a
        // DB-only "ready" would let the first search stall behind the
        // SapBERT/GLiNER load.
        if (!data.search_ready) throw new Error('not ready yet');
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

function StartupBanner({
  startup,
  retry,
}: {
  startup: StartupStatus;
  retry: () => void;
}): React.JSX.Element {
  if (startup === 'ready')
    return (
      <p className="terminology-demo__status terminology-demo__status--ready">
        ✅ <strong>Ready.</strong> Connected to the terminology demo Space (native FHIR R4 —{' '}
        <a href={`${SPACE_URL}/fhir/metadata`} target="_blank" rel="noreferrer">
          CapabilityStatement
        </a>
        ).
      </p>
    );
  if (startup === 'checking')
    return <p className="terminology-demo__status">🔎 Checking the terminology server…</p>;
  if (startup === 'starting')
    return (
      <p className="terminology-demo__status">
        🌅 <strong>Starting up</strong> — the demo Space sleeps when idle; waking it and
        loading the terminology models takes about a minute, and the first start after a
        cold stop can take up to about 2 minutes.
      </p>
    );
  return (
    <div className="terminology-demo__status terminology-demo__status--error">
      <p>
        ⚠️ <strong>Could not reach the demo Space</strong> after several minutes of trying.
        It may still be waking up, or temporarily unavailable.
      </p>
      <button className="button button--secondary" onClick={retry}>
        Retry
      </button>
    </div>
  );
}

function ModeCard({
  label,
  hint,
  rows,
}: {
  label: string;
  hint: string;
  rows: SearchRow[];
}): React.JSX.Element {
  const best = rows.length ? Math.max(...rows.map((r) => r.score ?? 0)) : 0;
  return (
    <div className="terminology-demo__card">
      <div className="terminology-demo__card-header">
        <strong>{label}</strong>
        <span className="terminology-demo__card-hint">{hint}</span>
      </div>
      {rows.length === 0 ? (
        <p className="terminology-demo__card-empty">no results</p>
      ) : (
        <ul className="terminology-demo__results">
          {rows.map((r, i) => {
            const isTop = r.score === best;
            return (
              <li
                key={i}
                className={`terminology-demo__result${isTop ? ' terminology-demo__top' : ''}`}>
                <span className="terminology-demo__display">
                  {isTop ? <strong>{r.display}</strong> : r.display}
                </span>
                <span className="terminology-demo__pills">
                  <span className="terminology-demo__pill terminology-demo__pill--system">
                    {r.systemName} <code>{r.code}</code>
                  </span>
                  {r.score != null && (
                    <span
                      className={`terminology-demo__pill terminology-demo__pill--score${
                        isTop ? ' terminology-demo__pill--best' : ''
                      }`}>
                      {r.score.toFixed(3)}
                    </span>
                  )}
                  {r.grade && (
                    <span className={GRADE_STYLES[r.grade] ?? 'terminology-demo__grade'}>
                      {r.grade}
                    </span>
                  )}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/** Live status of the demo Space: checking / starting / ready / failed. */
export function TerminologyDemoStatus(): React.JSX.Element {
  const {status: startup, retry} = useSpaceStartup();
  return <StartupBanner startup={startup} retry={retry} />;
}

/** The search half: query input, side-by-side mode cards. */
export function TerminologyDemoSearch(): React.JSX.Element {
  const [query, setQuery] = useState('myocardial infarction');
  const [state, setState] = useState<SearchState>({phase: 'idle'});
  const [modeRows, setModeRows] = useState<Record<string, SearchRow[]>>({});
  const {status: startup, notReady} = useSpaceStartup();
  const abortRef = useRef<AbortController | null>(null);

  const runSearch = useCallback(async () => {
    if (startup !== 'ready') return;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setState({phase: 'loading'});
    setModeRows({});
    try {
      // Each demo mode at once, top COMPARE_TOP each — the side-by-side
      // cards. (~3 requests of the 30/min budget.)
      const results = await Promise.all(
        SEARCH_MODES.map(
          async (m) => [m.id, await postSearch(query, m.id, COMPARE_TOP)] as const,
        ),
      );
      if (ac.signal.aborted) return;
      const byMode: Record<string, SearchRow[]> = {};
      for (const [mode, rows] of results) byMode[mode] = rows;
      setModeRows(byMode);
      setState({phase: 'done'});
    } catch {
      if (ac.signal.aborted) return;
      notReady();
      setState({
        phase: 'error',
        message:
          'Lost contact with the demo Space — it may have gone back to sleep. ' +
          'Waiting for it to come back; the form will re-enable when it is ready.',
      });
    }
  }, [query, startup, notReady]);

  const compareBody = useMemo(() => {
    if (state.phase === 'loading') return <p>Searching each mode…</p>;
    if (state.phase === 'error')
      return <p className="terminology-demo__error">⚠️ {state.message}</p>;
    if (state.phase === 'idle') return null;
    return (
      <div className="terminology-demo__grid">
        {SEARCH_MODES.map((m) => (
          <ModeCard key={m.id} label={m.label} hint={m.hint} rows={modeRows[m.id] ?? []} />
        ))}
      </div>
    );
  }, [state, modeRows]);

  return (
    <div className="terminology-demo">
      <div className="terminology-demo__controls">
        <input
          type="text"
          className="terminology-demo__input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && runSearch()}
          placeholder="e.g. heart attack, HbA1c, amoxicillin"
          maxLength={200}
          disabled={startup !== 'ready' || state.phase === 'loading'}
        />
        <button
          className="button button--primary terminology-demo__go"
          onClick={() => void runSearch()}
          disabled={startup !== 'ready' || state.phase === 'loading'}>
          Compare all modes
        </button>
      </div>
      {compareBody}
    </div>
  );
}

/** The annotations half: note textarea, extract button, annotation viewer. */
export function TerminologyDemoAnnotations(): React.JSX.Element {
  const [text, setText] = useState(
    'Patient with type 2 diabetes on metformin. Family history of heart attack.',
  );
  const [extractState, setExtractState] = useState<ExtractState>({phase: 'idle'});
  const [concepts, setConcepts] = useState<ExtractConcept[]>([]);
  const [termDetails, setTermDetails] = useState<TermDetail[]>([]);
  const [firstDetail, setFirstDetail] = useState<{
    lookup: ReturnType<typeof lookupSummary>;
    icd10?: string;
  } | null>(null);
  const {status: startup, notReady} = useSpaceStartup();

  // Patient-friendly canonical names for the first few extracted terms.
  // Defined before runExtract, which uses it.
  const enrichTerms = useCallback(async (found: ExtractConcept[]) => {
    const details: TermDetail[] = [];
    for (const concept of found.slice(0, MAX_TERM_DETAILS)) {
      const detail: TermDetail = {concept};
      try {
        // Canonical search: display IS the patient-friendly name.
        const rows = await postSearch(concept.matched_text ?? '', 'canonical', 1);
        if (rows[0]) detail.patientFriendly = rows[0].display;
      } catch {
        // best-effort enrichment
      }
      details.push(detail);
    }
    setTermDetails(details);
  }, []);

  // Extraction takes `text` explicitly so it can never run against a
  // stale closure capture of the note.
  const runExtract = useCallback(
    async (noteText: string) => {
      setExtractState({phase: 'loading'});
      setConcepts([]);
      setTermDetails([]);
      setFirstDetail(null);
      try {
        const res = await fetch(`${SPACE_URL}/fhir/CodeSystem/$extract`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: fhirParams([
            ['text', noteText],
            ['format', 'codes'],
            // Show the full assertion picture in the demo: family-history
            // findings are opt-in on $extract (default false).
            ['includeFamily', true],
          ]),
        });
        if (res.status === 503) {
          notReady();
          return;
        }
        if (!res.ok) throw new Error(`Space answered ${res.status}`);
        const bundle = (await res.json()) as {entry?: Array<{resource?: ExtractConcept}>};
        const found = (bundle.entry ?? [])
          .map((e) => e.resource ?? {})
          .filter((c) => c.matched_text);
        setConcepts(found);
        setExtractState({phase: 'idle'});
        void enrichTerms(found);
        // Code detail + one ICD-10-CM mapping for the FIRST concept only.
        const first = found[0];
        if (first?.system && first?.code) {
          try {
            const res2 = await fetch(`${SPACE_URL}/fhir/CodeSystem/$lookup`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: fhirParams([
                ['system', first.system],
                ['code', first.code],
              ]),
            });
            if (!res2.ok) return;
            const params = (await res2.json()) as {parameter?: FhirParam[]};
            const detail: {lookup: ReturnType<typeof lookupSummary>; icd10?: string} = {
              lookup: lookupSummary(params),
            };
            try {
              const tr = await fetch(`${SPACE_URL}/fhir/ConceptMap/$translate`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: fhirParams([
                  ['system', first.system ?? ''],
                  ['code', first.code ?? ''],
                  ['targetSystem', 'http://hl7.org/fhir/sid/icd-10-cm'],
                ]),
              });
              if (tr.ok) {
                const tp = (await tr.json()) as {parameter?: FhirParam[]};
                const match = translateMatches(tp)[0];
                if (match?.coding?.code) {
                  detail.icd10 = `${match.coding.code} — ${match.coding.display ?? ''}`.trim();
                }
              }
            } catch {
              // best-effort mapping
            }
            setFirstDetail(detail);
          } catch {
            // best-effort detail
          }
        }
      } catch {
        setExtractState({
          phase: 'error',
          message: 'Extraction failed — the Space may have gone back to sleep.',
        });
      }
    },
    [notReady, enrichTerms],
  );

  // Annotation viewer: the note with each identified term highlighted,
  // status-tinted, carrying its details inline — no separate table.
  const annotatedNote = useMemo(() => {
    if (!concepts.length) return null;
    const friendlyByTerm = new Map(
      termDetails.map((d) => [d.concept.matched_text, d.patientFriendly]),
    );
    const marks = concepts
      .filter((c) => c.span_start != null && c.span_end != null)
      .sort((a, b) => (a.span_start ?? 0) - (b.span_start ?? 0));
    if (!marks.length) return null;
    const parts: React.ReactNode[] = [];
    let cursor = 0;
    marks.forEach((c, i) => {
      if ((c.span_start ?? 0) > cursor)
        parts.push(<span key={`t${i}`}>{text.slice(cursor, c.span_start)}</span>);
      const status = c.status ?? 'affirmed';
      parts.push(
        <span key={`m${i}`} className={`terminology-demo__term terminology-demo__term--${status}`}>
          <mark className="terminology-demo__mark">
            {text.slice(c.span_start, c.span_end)}
          </mark>
          {(c.display || (c.source && c.code)) && (
            <span className="terminology-demo__code-pill">
              {c.display}
              {c.display !== friendlyByTerm.get(c.matched_text) &&
              friendlyByTerm.get(c.matched_text)
                ? ` · ${friendlyByTerm.get(c.matched_text)}`
                : ''}
              {c.source && c.code ? ` · ${sourceShortName(c.source)} ${c.code}` : ''}
            </span>
          )}
          <span className="terminology-demo__term-tag">{status}</span>
        </span>,
      );
      cursor = c.span_end ?? cursor;
    });
    if (cursor < text.length) parts.push(<span key="tail">{text.slice(cursor)}</span>);
    return <div className="terminology-demo__note">{parts}</div>;
  }, [concepts, termDetails, text]);

  const busy = startup !== 'ready' || extractState.phase === 'loading';

  return (
    <div className="terminology-demo">
      <div className="terminology-demo__controls terminology-demo__controls--column">
        <textarea
          className="terminology-demo__input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          maxLength={4000}
          disabled={busy}
        />
        <button
          className="button button--secondary terminology-demo__go"
          onClick={() => void runExtract(text)}
          disabled={busy}>
          {extractState.phase === 'loading' ? 'Extracting…' : 'Extract concepts'}
        </button>
      </div>
      {extractState.phase === 'error' && (
        <p className="terminology-demo__error">⚠️ {extractState.message}</p>
      )}
      {annotatedNote}
      {firstDetail && (firstDetail.lookup.display || firstDetail.lookup.systemName) && (
        <p className="terminology-demo__codedetail">
          <strong>{firstDetail.lookup.display ?? firstDetail.lookup.systemName}</strong>
          {firstDetail.lookup.cui ? (
            <>
              {' '}— UMLS CUI <code>{firstDetail.lookup.cui}</code>
            </>
          ) : null}
          {firstDetail.icd10 ? (
            <>
              {' '}· ICD-10-CM <code>{firstDetail.icd10}</code>
            </>
          ) : null}
        </p>
      )}
    </div>
  );
}
