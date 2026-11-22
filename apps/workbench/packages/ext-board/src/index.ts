import type { ExtensionManifest } from '@calli/extension-api';
import type { WebExtensionModule } from '@calli/extension-api/web';
import manifest from '../manifest.json';
import { BoardView } from './BoardView';
import './board.css';

const extension: WebExtensionModule = {
  manifest: manifest as ExtensionManifest,
  views: { board: BoardView },
};

export default extension;
