import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const baseUrl = process.env.BASE_URL || '/';

const config: Config = {
  title: 'Medical Terminology for Data Science',
  tagline: 'UMLS-backed terminology workflows for Python notebooks and data science',
  favicon: 'img/icon.svg',

  future: {
    v4: true,
  },

  url: 'https://terminology.fhir4ds.com',
  baseUrl,

  organizationName: 'fhir4ds',
  projectName: 'medterm4ds',

  onBrokenLinks: 'warn',
  markdown: {
    mermaid: true,
    hooks: {
      onBrokenMarkdownLinks: 'warn',
    },
  },

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  themes: ['@docusaurus/theme-mermaid'],

  plugins: [
    [
      '@docusaurus/plugin-client-redirects',
      {
        redirects: [
          {from: '/docs/terminology/supported-sources', to: '/docs/getting-started/supported-sources'},
          {from: '/docs/terminology/umls-release-info', to: '/docs/getting-started/umls-release-info'},
          {from: '/docs/terminology/source-inventory', to: '/docs/getting-started/source-inventory'},
          {from: '/docs/terminology/ndc-rxnorm', to: '/docs/capabilities/code-resolution'},
          {from: '/docs/terminology/obsolete-codes', to: '/docs/capabilities/code-resolution'},
        ],
      },
    ],
  ],

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          editUrl: 'https://github.com/medterm4ds/medterm4ds/edit/main/web/website/',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    image: 'img/icon.svg',
    colorMode: {
      defaultMode: 'dark',
      disableSwitch: true,
      respectPrefersColorScheme: false,
    },
    mermaid: {
      theme: {light: 'base', dark: 'base'},
      options: {
        themeVariables: {
          background: '#08111f',
          primaryColor: '#111d2d',
          primaryTextColor: '#f4f9ff',
          primaryBorderColor: '#54c6ff',
          lineColor: '#8ee6c1',
          secondaryColor: '#0b1525',
          tertiaryColor: '#030915',
          edgeLabelBackground: '#08111f',
          clusterBkg: '#0b1525',
          clusterBorder: '#54c6ff',
        },
      },
    },
    navbar: {
      title: 'MEDTERM4DS',
      logo: {
        alt: 'medterm4ds logo',
        src: 'img/icon-dark.svg',
        srcDark: 'img/icon-dark.svg',
      },
      items: [
        {to: '/docs/getting-started/quickstart', label: 'Getting Started', position: 'left'},
        {to: '/docs/capabilities/code-lookup', label: 'Capabilities', position: 'left'},
        {to: '/docs/interfaces/python', label: 'Interfaces', position: 'left'},
        {to: '/docs/api-reference/medterm4ds', label: 'API', position: 'left'},
        {to: '/docs/examples/notebooks/overview', label: 'Examples', position: 'left'},
        {
          href: 'https://github.com/medterm4ds/medterm4ds',
          position: 'right',
          className: 'navbar-github-link',
          'aria-label': 'GitHub repository',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            {label: 'Quickstart', to: '/docs/getting-started/quickstart'},
            {label: 'First Notebook', to: '/docs/getting-started/first-notebook'},
            {label: 'Data Setup', to: '/docs/getting-started/data-setup'},
            {label: 'Python', to: '/docs/interfaces/python'},
          ],
        },
        {
          title: 'Data Sources',
          items: [
            {label: 'Supported Sources', to: '/docs/getting-started/supported-sources'},
            {label: 'UMLS Release Info', to: '/docs/getting-started/umls-release-info'},
            {label: 'Source Inventory', to: '/docs/getting-started/source-inventory'},
          ],
        },
        {
          title: 'Production Recipes',
          items: [
            {label: 'ValueSets', to: '/docs/examples/recipes/valuesets'},
            {label: 'ConceptMaps', to: '/docs/examples/recipes/conceptmaps'},
            {label: 'Quality Review', to: '/docs/examples/recipes/quality-review'},
          ],
        },
        {
          title: 'Resources',
          items: [
            {label: 'API Reference', to: '/docs/api-reference/medterm4ds'},
            {label: 'Notebook Examples', to: '/docs/examples/notebooks/overview'},
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} Medical Terminology for Data Science contributors. Built with Docusaurus.`,
    },
    prism: {
      theme: prismThemes.oneDark,
      darkTheme: prismThemes.oneDark,
      additionalLanguages: ['python', 'sql', 'bash', 'json'],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
