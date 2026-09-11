import type { ClientLink } from '@orpc/client'
import type { ConsoleClientContext } from '../client'
import { DynamicLink } from '@orpc/client'
import { OpenAPILink } from '@orpc/openapi-client/fetch'
import { env } from '@/env'
// eslint-disable-next-line no-restricted-imports -- Keep native session, CSRF and error handling at the existing transport boundary.
import { request } from '../base'

export function withEnterpriseBusinessLink(
  nativeLink: ClientLink<ConsoleClientContext>,
  getRootURL: () => URL,
): ClientLink<ConsoleClientContext> {
  let businessLink: Promise<ClientLink<ConsoleClientContext>> | undefined

  function getBusinessURL() {
    const url = new URL(getRootURL())
    url.pathname = `${env.NEXT_PUBLIC_BASE_PATH}/`
    return url
  }

  function getBusinessLink() {
    businessLink ??= import('@enterprise/business-contracts/orpc')
      .then(
        ({ contract }) =>
          new OpenAPILink<ConsoleClientContext>(
            { business: contract },
            {
              url: getBusinessURL(),
              fetch: (input, init, options) =>
                request(input.url, init, {
                  fetchCompat: true,
                  request: input,
                  silent: options.context.silent,
                }),
            },
          ),
      )
      .catch((error: unknown) => {
        businessLink = undefined
        throw error
      })
    return businessLink
  }

  return new DynamicLink<ConsoleClientContext>((_options, path) =>
    path[0] === 'business' ? getBusinessLink() : nativeLink,
  )
}
