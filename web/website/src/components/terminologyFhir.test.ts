/**
 * Unit tests for the pure FHIR-shape helpers used by TerminologyDemo.
 *
 * The fixtures are REAL responses captured from the live Space
 * (fhir4ds-terminology-demo-server.hf.space, 2026-11-03) — the ranking
 * lives on the Bundle ENTRY's `search` element, never on the resource.
 * These tests exist so a path regression (e.g. reading
 * e.resource.search.score, which is always undefined and renders blank
 * cells) fails loudly instead of shipping blanks.
 *
 * Run: npm test  (node --test; Node >= 22 strips types natively)
 */
import assert from 'node:assert/strict';
import {test} from 'node:test';

import {
  entryGrade,
  entryScore,
  fhirParams,
  lookupSummary,
  searchRows,
  systemShortName,
  translateMatches,
} from './terminologyFhir.ts';

// Captured verbatim from POST /fhir/CodeSystem/$search
// {query: "heart attack", searchMode: "hybrid", count: 3} on the live Space.
const LIVE_SEARCH_BUNDLE = {
  resourceType: 'Bundle',
  type: 'searchset',
  total: 3,
  entry: [
    {
      fullUrl: 'CodeSystem/http://snomed.info/sct-42531007',
      resource: {
        system: 'http://snomed.info/sct',
        code: '42531007',
        display: 'Microinfarct of heart',
      },
      search: {
        mode: 'match',
        score: 1.0,
        extension: [
          {
            url: 'http://fhir4ds.org/fhir/StructureDefinition/match-grade',
            valueCode: 'certain',
          },
        ],
      },
    },
    {
      fullUrl: 'CodeSystem/http://snomed.info/sct-22298006',
      resource: {
        system: 'http://snomed.info/sct',
        code: '22298006',
        display: 'Myocardial infarction',
      },
      search: {
        mode: 'match',
        score: 0.7642,
        extension: [
          {
            url: 'http://fhir4ds.org/fhir/StructureDefinition/match-grade',
            valueCode: 'probable',
          },
        ],
      },
    },
  ],
};

test('entryScore reads the ENTRY-level search.score (live shape)', () => {
  const [first, second] = LIVE_SEARCH_BUNDLE.entry;
  assert.equal(entryScore(first), 1.0);
  assert.equal(entryScore(second), 0.7642);
});

test('entryScore does NOT read resource.search (the blank-cell regression)', () => {
  const entry = {resource: {code: 'x', search: {score: 0.9}}} as never;
  assert.equal(entryScore(entry), undefined);
});

test('entryGrade reads the match-grade extension from the entry', () => {
  const [first, second] = LIVE_SEARCH_BUNDLE.entry;
  assert.equal(entryGrade(first), 'certain');
  assert.equal(entryGrade(second), 'probable');
});

test('searchRows produces distinct, renderable score/grade per row', () => {
  const rows = searchRows(LIVE_SEARCH_BUNDLE);
  assert.equal(rows.length, 2);
  assert.deepEqual(
    rows.map((r) => r.score),
    [1.0, 0.7642],
  );
  assert.deepEqual(
    rows.map((r) => r.grade),
    ['certain', 'probable'],
  );
  assert.equal(rows[1].code, '22298006');
  assert.equal(rows[1].display, 'Myocardial infarction');
});

test('systemShortName maps FHIR URIs to compact names', () => {
  assert.equal(systemShortName('http://snomed.info/sct'), 'SNOMED CT');
  assert.equal(systemShortName('http://loinc.org'), 'LOINC');
  assert.equal(systemShortName('http://hl7.org/fhir/sid/icd-10-cm'), 'ICD-10-CM');
  // searchRows carries the short name alongside the URI
  const rows = searchRows(LIVE_SEARCH_BUNDLE);
  assert.equal(rows[1].systemName, 'SNOMED CT');
  // unknown URIs degrade to a trimmed host, never a long raw URL
  assert.equal(
    systemShortName('https://example.org/CodeSystem/foo/'),
    'example.org/CodeSystem/foo',
  );
});

test('fhirParams builds a FHIR Parameters body with typed values', () => {
  const parsed = JSON.parse(
    fhirParams([
      ['query', 'heart attack'],
      ['count', 3],
      ['includeFamily', true],
    ]),
  );
  assert.equal(parsed.resourceType, 'Parameters');
  assert.deepEqual(parsed.parameter, [
    {name: 'query', valueString: 'heart attack'},
    {name: 'count', valueInteger: 3},
    {name: 'includeFamily', valueBoolean: true},
  ]);
});

// Captured verbatim from POST /fhir/ConceptMap/$translate (SNOMED 22298006
// -> ICD-10-CM) on the live Space.
const LIVE_TRANSLATE_PARAMS = {
  resourceType: 'Parameters',
  parameter: [
    {name: 'result', valueBoolean: true},
    {name: 'message', valueString: '2 matches found'},
    {
      name: 'match',
      part: [
        {name: 'equivalence', valueCode: 'equivalent'},
        {
          name: 'concept',
          valueCoding: {
            system: 'http://hl7.org/fhir/sid/icd-10-cm',
            code: 'I21',
            display: 'cardiac infarction',
          },
        },
      ],
    },
  ],
};

test('translateMatches extracts equivalence + target coding', () => {
  const matches = translateMatches(LIVE_TRANSLATE_PARAMS);
  assert.equal(matches.length, 1);
  assert.equal(matches[0].equivalence, 'equivalent');
  assert.equal(matches[0].coding?.code, 'I21');
  assert.equal(matches[0].coding?.display, 'cardiac infarction');
});

// Captured verbatim from POST /fhir/CodeSystem/$lookup
// (SNOMED 22298006) on the live Space.
const LIVE_LOOKUP_PARAMS = {
  resourceType: 'Parameters',
  parameter: [
    {name: 'name', valueString: 'SNOMED Clinical Terms (US)'},
    {name: 'code', valueCode: '22298006'},
    {name: 'system', valueUri: 'http://snomed.info/sct'},
    {name: 'display', valueString: 'Myocardial infarction'},
    {
      name: 'property',
      part: [
        {name: 'code', valueCode: 'cui'},
        {name: 'value', valueString: 'C0027051'},
      ],
    },
  ],
};

test('lookupSummary extracts display and the UMLS CUI', () => {
  const summary = lookupSummary(LIVE_LOOKUP_PARAMS);
  assert.equal(summary.display, 'Myocardial infarction');
  assert.equal(summary.cui, 'C0027051');
});
