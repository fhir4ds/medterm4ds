/**
 * Pure helpers for reading the live FHIR shapes returned by the
 * terminology-demo-server Space. Kept free of React so they can be unit
 * tested directly (node --test) — see terminologyFhir.test.ts.
 *
 * In a FHIR search Bundle the ranking lives on the ENTRY, not inside the
 * resource: e.search.score and e.search.extension[] carry the score and
 * the match-grade; e.resource.search does not exist.
 */

export type FhirParam = {
  name: string;
  valueString?: string;
  valueCode?: string;
  valueUri?: string;
  valueBoolean?: boolean;
  valueInteger?: number;
  part?: FhirParam[];
};

export type FhirCoding = {system?: string; code?: string; display?: string};

export type FhirParamWithCoding = FhirParam & {valueCoding?: FhirCoding};

export type FhirSearchEntry = {
  fullUrl?: string;
  resource?: FhirCoding;
  search?: {
    mode?: string;
    score?: number;
    extension?: Array<{url?: string; valueCode?: string}>;
  };
};

const MATCH_GRADE_EXT = 'match-grade';

export function entryScore(entry: FhirSearchEntry): number | undefined {
  return entry.search?.score;
}

export function entryGrade(entry: FhirSearchEntry): string | undefined {
  const exts = entry.search?.extension;
  if (!exts) return undefined;
  for (const ext of exts) {
    if (ext.url?.endsWith(MATCH_GRADE_EXT)) return ext.valueCode;
  }
  return undefined;
}

export type SearchRow = {
  display?: string;
  code?: string;
  system?: string;
  score?: number;
  grade?: string;
};

export function searchRows(bundle: {entry?: FhirSearchEntry[]}): SearchRow[] {
  return (bundle.entry ?? []).map((e) => ({
    display: e.resource?.display,
    code: e.resource?.code,
    system: e.resource?.system,
    score: entryScore(e),
    grade: entryGrade(e),
  }));
}

/** Build a FHIR Parameters request body from plain name->value pairs. */
export function fhirParams(
  pairs: Array<[string, string | number | boolean]>,
): string {
  return JSON.stringify({
    resourceType: 'Parameters',
    parameter: pairs.map(([name, value]) => ({
      name,
      ...(typeof value === 'number'
        ? {valueInteger: value}
        : typeof value === 'boolean'
          ? {valueBoolean: value}
          : {valueString: value}),
    })),
  });
}

/** Summarise a $translate Parameters response into its matches. */
export type TranslateMatch = {equivalence?: string; coding?: FhirCoding};

export function translateMatches(params: {parameter?: FhirParam[]}): TranslateMatch[] {
  return (params.parameter ?? [])
    .filter((p) => p.name === 'match')
    .map((m) => ({
      equivalence: m.part?.find((p) => p.name === 'equivalence')?.valueCode,
      coding: (m.part?.find((p) => p.name === 'concept') as FhirParamWithCoding | undefined)
        ?.valueCoding,
    }));
}

/** The display line for a $lookup Parameters response, with UMLS CUI. */
export function lookupSummary(params: {parameter?: FhirParam[]}): {
  display?: string;
  systemName?: string;
  cui?: string;
} {
  const parameter = params.parameter ?? [];
  const get = (n: string) => parameter.find((p) => p.name === n);
  const cui = parameter.find(
    (p) => p.name === 'property' && p.part?.[0]?.valueCode === 'cui',
  )?.part?.[1]?.valueString;
  return {
    display: get('display')?.valueString,
    systemName: get('name')?.valueString,
    cui,
  };
}
