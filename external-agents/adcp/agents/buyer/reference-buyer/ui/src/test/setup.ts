/**
 * Test environment shims for APIs jsdom does not implement.
 *
 * Deliberately minimal: anything stubbed here is a browser API the code under test only
 * queries, never a behaviour of ours. Stubbing our own behaviour would test the stub.
 */

// Read by the motion helpers to honour a reduced-motion preference. jsdom has no matchMedia.
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}

// Called by MessageList to keep the transcript pinned to the newest message. jsdom implements no
// layout, so it provides no scrollIntoView at all -- without this, any test that renders a message
// fails on the scroll rather than on anything it was checking.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}
