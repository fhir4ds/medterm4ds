import {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  mainSidebar: [
    {
      type: 'category',
      label: 'Getting Started',
      link: {type: 'doc', id: 'getting-started/quickstart'},
      items: [
        'getting-started/quickstart',
        'getting-started/installation',
        'getting-started/first-notebook',
        'getting-started/architecture',
        'getting-started/engine-configuration',
        'getting-started/remote-engine',
        'getting-started/licensing',
        'getting-started/releases',
      ],
    },
    {
      type: 'category',
      label: 'Capabilities',
      link: {type: 'doc', id: 'capabilities/code-lookup'},
      items: [
        'capabilities/code-lookup',
        'capabilities/hierarchy-traversal',
        'capabilities/cross-system-mapping',
        'capabilities/code-resolution',
        'capabilities/patient-friendly-names',
        'capabilities/text-search',
        'capabilities/text-extraction',
      ],
    },
    {
      type: 'category',
      label: 'Interfaces',
      link: {type: 'doc', id: 'interfaces/python'},
      items: [
        'interfaces/python',
        'interfaces/cli',
        'interfaces/mcp-server',
        'interfaces/fhir-server',
        'interfaces/docker-deployment',
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
      label: 'API Reference',
      link: {type: 'doc', id: 'api-reference/medterm4ds'},
      items: [
        'api-reference/medterm4ds',
        'api-reference/terminology-client',
        'api-reference/models',
        'api-reference/service-functions',
      ],
    },
    {
      type: 'category',
      label: 'Examples',
      items: [
        'examples/notebooks/overview',
        {
          type: 'category',
          label: 'Production Recipes',
          items: [
            'examples/recipes/patient-friendly-names',
            'examples/recipes/icd10-to-snomed',
            'examples/recipes/text-to-code-search',
            'examples/recipes/clinical-note-extraction',
            'examples/recipes/fhir-server',
            'examples/recipes/bulk-exports',
            'examples/recipes/conceptmaps',
            'examples/recipes/notebooks',
            'examples/recipes/quality-review',
            'examples/recipes/valuesets',
            'examples/recipes/output-formats',
          ],
        },
      ],
    },
  ],
};

export default sidebars;
