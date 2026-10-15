import { defineConfig } from 'tsdown'

export default defineConfig({
  name: 'dsh-loopx-plugin/host',
  entry: {
    index: 'build-temp/host/index.js',
    'init-command': 'build-temp/host/init-command.js',
    driver: 'build-temp/host/driver.js',
    observer: 'build-temp/host/observer.js',
    'native-operation-context': 'build-temp/host/native-operation-context.js',
    'native-operation': 'build-temp/host/native-operation.js',
  },
  outDir: 'lib',
  format: ['esm'],
  platform: 'node',
  target: 'es2024',
  fixedExtension: false,
  dts: false,
  clean: false,
  sourcemap: false,
})
