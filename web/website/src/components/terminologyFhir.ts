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

/**
 * Short human name for a FHIR system URI (keeps the demo compact — no
 * long URLs, no horizontal scrolling).
 */
const SYSTEM_SHORT_NAMES: Record<string, string> = {
  'http://snomed.info/sct': 'SNOMED CT',
  'http://loinc.org': 'LOINC',
  'http://hl7.org/fhir/sid/icd-10-cm': 'ICD-10-CM',
  'http://hl7.org/fhir/sid/icd-10-pcs': 'ICD-10-PCS',
  'http://www.nlm.nih.gov/research/umls/rxnorm': 'RxNorm',
  'http://hl7.org/fhir/sid/cvx': 'CVX',
  'http://terminology.hl7.org/CodeSystem/hcpcs': 'HCPCS',
};

export function systemShortName(system?: string): string {
  if (!system) return '';
  const mapped = SYSTEM_SHORT_NAMES[system];
  if (mapped) return mapped;
  // RxNorm has several URI spellings; cover the common prefix.
  if (system.includes('rxnorm')) return 'RxNorm';
  if (system.includes('icd-10-cm')) return 'ICD-10-CM';
  if (system.includes('icd-10-pcs')) return 'ICD-10-PCS';
  return system.replace(/^https?:\/\//, '').replace(/\/$/, '');
}

/**
 * Short human name for a raw UMLS source label (as carried on extracted
 * concepts, e.g. SNOMEDCT_US / RXNORM / LNC).
 */
const SOURCE_SHORT_NAMES: Record<string, string> = {
  SNOMEDCT_US: 'SNOMED CT',
  RXNORM: 'RxNorm',
  LNC: 'LOINC',
  ICD10CM: 'ICD-10-CM',
  ICD10PCS: 'ICD-10-PCS',
  CVX: 'CVX',
  HCPCS: 'HCPCS',
};

export function sourceShortName(source?: string): string {
  if (!source) return '';
  return SOURCE_SHORT_NAMES[source.toUpperCase()] ?? source;
}

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
  systemName?: string;
  score?: number;
  grade?: string;
};

export function searchRows(bundle: {entry?: FhirSearchEntry[]}): SearchRow[] {
  return (bundle.entry ?? []).map((e) => ({
    display: e.resource?.display,
    code: e.resource?.code,
    system: e.resource?.system,
    systemName: systemShortName(e.resource?.system),
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
