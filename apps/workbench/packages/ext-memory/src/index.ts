import type { ExtensionManifest } from '@calli/extension-api';
import type { WebExtensionModule } from '@calli/extension-api/web';
import manifest from '../manifest.json';
import { MemoryView } from './MemoryView';
import './memory.css';

const extension: WebExtensionModule = { manifest: manifest as ExtensionManifest, views: { memory: MemoryView } };
export default extension;
