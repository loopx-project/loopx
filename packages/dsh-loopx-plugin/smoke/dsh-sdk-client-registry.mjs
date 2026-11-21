// DSH 0.2.2 passes the same-name Loader entries captured at flush start.
// Older qualified SDKs collect them inside processOne. This test adapter
// preserves the real SDK implementation and each caller's discovery oracle.
export function reconcileClientPackage(registry, packageId, onError) {
  return registry.processOne.length >= 3
    ? registry.processOne(packageId, [...registry.ctx.loader.entries()], onError)
    : registry.processOne(packageId, onError)
}
