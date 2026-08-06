/** Resolve browser asset imports to their source files during Node tests. */
import { registerHooks } from 'node:module';

const sharedAssetsUrl = new URL('../../src/shared/static/', import.meta.url);

registerHooks({
  resolve(specifier, context, nextResolve) {
    const sharedPrefix = '/assets/shared/';
    if (specifier.startsWith(sharedPrefix)) {
      return {
        shortCircuit: true,
        url: new URL(specifier.slice(sharedPrefix.length), sharedAssetsUrl).href,
      };
    }
    return nextResolve(specifier, context);
  },
});
