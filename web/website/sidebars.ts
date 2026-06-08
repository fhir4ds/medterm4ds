import {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  mainSidebar: [
    {
      type: 'category',
      label: 'Getting Started',
      link: {type: 'doc', id: 'getting-started/quickstart'},
      items: [
        'getting-started/quickstart',
        'getting-started/first-notebook',
        'getting-started/installation',
        'getting-started/data-setup',
        'getting-started/licensing',
        'getting-started/releases',
        'getting-started/architecture',
      ],
    },
    {
      type: 'category',
      label: 'User Guide',
      link: {type: 'doc', id: 'user-guide/interfaces/python'},
      items: [
        {
          type: 'category',
          label: 'Engines',
          items: [
            'user-guide/engines/local-duckdb',
            'user-guide/engines/remote-api',
          ],
        },
        {
          type: 'category',
          label: 'Interfaces',
          items: [
            'user-guide/interfaces/python',
            'user-guide/interfaces/cli',
            'user-guide/interfaces/api-server',
            'user-guide/interfaces/mcp-server',
          ],
        },
        {
          type: 'category',
          label: 'Core Functions',
          items: [
            'user-guide/core-functions/lookup',
            'user-guide/core-functions/resolve',
            'user-guide/core-functions/patient-friendly',
            'user-guide/core-functions/mapping',
            'user-guide/core-functions/hierarchy',
            'user-guide/core-functions/optimize',
            'user-guide/core-functions/discovery',
          ],
        },
        {
          type: 'category',
          label: 'Output Formats',
          items: [
            'user-guide/output-formats/structured',
            'user-guide/output-formats/compact',
            'user-guide/output-formats/fhir-conceptmap',
            'user-guide/output-formats/dataframes',
          ],
        },
      ],
    },
    {
      type: 'category',
      label: 'Terminology',
      link: {type: 'doc', id: 'terminology/supported-sources'},
      items: [
        'terminology/supported-sources',
        'terminology/umls-release-info',
        'terminology/source-inventory',
        'terminology/ndc-rxnorm',
        'terminology/obsolete-codes',
      ],
    },
    {
      type: 'category',
      label: 'Workflows',
      link: {type: 'doc', id: 'workflows/valuesets'},
      items: [
        'workflows/notebooks',
        'workflows/valuesets',
        'workflows/conceptmaps',
        'workflows/bulk-exports',
        'workflows/quality-review',
      ],
    },
    {
      type: 'category',
      label: 'API Reference',
      link: {type: 'doc', id: 'api-reference/medterm4ds'},
      items: [
        'api-reference/medterm4ds',
        'api-reference/terminology-client',
        'api-reference/terminology-methods',
        'api-reference/models',
        'api-reference/service-functions',
        'api-reference/data-setup',
        'api-reference/engines-outputs',
      ],
    },
    {
      type: 'category',
      label: 'Examples',
      items: [
        'examples/notebooks/overview',
        {
          type: 'category',
          label: 'Recipes',
          items: [
            'examples/recipes/patient-friendly-names',
            'examples/recipes/icd10-to-snomed',
            'examples/recipes/ndc-to-rxcui',
            'examples/recipes/valueset-optimization',
            'examples/recipes/mcp-tools',
          ],
        },
      ],
    },
  ],
};

export default sidebars;
