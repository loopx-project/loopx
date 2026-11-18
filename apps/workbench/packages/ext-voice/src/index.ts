import type { ExtensionManifest } from '@calli/extension-api';
import type { WebExtensionModule } from '@calli/extension-api/web';
import manifest from '../manifest.json';

const extension: WebExtensionModule = {
  manifest: manifest as ExtensionManifest,
  views: {},
};

export default extension;
