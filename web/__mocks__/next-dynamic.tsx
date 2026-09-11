import type { ComponentType } from 'react'
import { lazy, Suspense } from 'react'

/** Load the real component without Next's server/client runtime boundary in DOM tests. */
export default function dynamic<P extends object>(
  loader: () => Promise<{ default: ComponentType<P> }>,
) {
  const Component = lazy(loader)
  return function DynamicTestComponent(props: P) {
    return (
      <Suspense fallback={null}>
        <Component {...props} />
      </Suspense>
    )
  }
}
