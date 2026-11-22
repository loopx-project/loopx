import type { ExtensionManifest } from '@calli/extension-api';
import type { WebExtensionModule } from '@calli/extension-api/web';
import manifest from '../manifest.json';
import { AutoTitleView } from './AutoTitleView';
import './autotitle.css';

const extension: WebExtensionModule = { manifest: manifest as ExtensionManifest, views: { autotitle: AutoTitleView } };
export default extension;
