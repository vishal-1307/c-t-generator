/**
 * Root loading state, shown while a route segment's code is still arriving.
 *
 * Deliberately plain: pages fetch their own data on mount and render their own
 * skeletons, so this covers only the navigation gap. Anything more elaborate
 * would flash and then be replaced, which reads as slower rather than faster.
 */
export default function Loading() {
  return (
    <div className="py-16" aria-busy="true" aria-live="polite">
      <div className="mx-auto max-w-sm space-y-3">
        <div className="h-4 w-32 animate-pulse rounded bg-line" />
        <div className="h-24 animate-pulse rounded-md bg-surface-hover" />
        <span className="sr-only">Loading</span>
      </div>
    </div>
  );
}
