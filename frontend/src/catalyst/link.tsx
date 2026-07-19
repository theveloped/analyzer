/**
 * Link component for Catalyst. The analyzer front-end has no client router, so
 * this renders a plain anchor inside Headless UI's DataInteractive wrapper
 * (which is all the Catalyst components need). If routing is added later, swap
 * the inner <a> for the router's Link, as in the upstream Catalyst kit.
 */

import * as Headless from '@headlessui/react'
import React, { forwardRef } from 'react'

export const Link = forwardRef(function Link(
  props: { href: string } & React.ComponentPropsWithoutRef<'a'>,
  ref: React.ForwardedRef<HTMLAnchorElement>
) {
  return (
    <Headless.DataInteractive>
      {/* eslint-disable-next-line jsx-a11y/anchor-has-content */}
      <a {...props} ref={ref} />
    </Headless.DataInteractive>
  )
})
