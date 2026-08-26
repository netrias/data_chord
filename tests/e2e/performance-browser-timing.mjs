const _RESOURCE_TYPES = new Set([
  'css',
  'fetch',
  'font',
  'img',
  'link',
  'script',
  'xmlhttprequest',
]);

const _number = (value) => (Number.isFinite(value) ? value : null);

const _deliveryType = (value) => (
  typeof value === 'string' && value.length > 0 ? value : null
);

const _privatePath = (pathname) => pathname.replace(
  /^(\/stage-4\/(?:non-conformant|overrides))\/[^/]+$/,
  '$1/:file_id',
);

const _navigationTiming = (entry) => ({
  type: typeof entry?.type === 'string' ? entry.type : 'navigate',
  response_start_ms: _number(entry?.responseStart),
  response_end_ms: _number(entry?.responseEnd),
  dom_content_loaded_ms: _number(entry?.domContentLoadedEventEnd),
  load_event_end_ms: _number(entry?.loadEventEnd),
  transfer_size_bytes: _number(entry?.transferSize),
  encoded_body_size_bytes: _number(entry?.encodedBodySize),
  decoded_body_size_bytes: _number(entry?.decodedBodySize),
  delivery_type: _deliveryType(entry?.deliveryType),
});

const _resourceTiming = (entry, origin) => {
  if (!_RESOURCE_TYPES.has(entry.initiatorType)) return null;
  let resourceUrl;
  try {
    resourceUrl = new URL(entry.name);
  } catch {
    return null;
  }
  if (resourceUrl.origin !== origin) return null;

  return {
    path: _privatePath(resourceUrl.pathname),
    initiator_type: entry.initiatorType,
    start_ms: _number(entry.startTime),
    response_start_ms: _number(entry.responseStart),
    response_end_ms: _number(entry.responseEnd),
    transfer_size_bytes: _number(entry.transferSize),
    encoded_body_size_bytes: _number(entry.encodedBodySize),
    decoded_body_size_bytes: _number(entry.decodedBodySize),
    delivery_type: _deliveryType(entry.deliveryType),
  };
};

export const normalizeBrowserTiming = ({ origin, navigation, resources }) => {
  if (!navigation) throw new Error('Browser navigation timing is unavailable.');
  return {
    navigation: _navigationTiming(navigation),
    resources: resources
      .map((entry) => _resourceTiming(entry, origin))
      .filter((entry) => entry !== null),
  };
};

export const captureBrowserTiming = async (page) => {
  const timing = await page.evaluate(() => {
    const navigation = performance.getEntriesByType('navigation')[0];
    return {
      origin: window.location.origin,
      navigation: navigation?.toJSON?.() ?? navigation ?? null,
      resources: performance.getEntriesByType('resource').map(
        (resource) => resource.toJSON?.() ?? resource,
      ),
    };
  });
  return normalizeBrowserTiming(timing);
};
