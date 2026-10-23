/**
 * Rendering the agent's replies as markdown.
 *
 * Sanitised with DOMPurify before it reaches the DOM. The text comes from a model, so it is
 * untrusted regardless of how the transport is authenticated: a reply that echoes seller-supplied
 * content is carrying text this UI never wrote.
 *
 * Applied only to agent replies. Raw payloads stay monospace text, where escaping is what makes
 * them readable rather than a hazard to remove.
 */

import DOMPurify from 'dompurify';
import { marked } from 'marked';

marked.setOptions({
  // Line breaks as written. Model output uses single newlines to mean a break far more often than
  // it means a wrapped paragraph.
  breaks: true,
  gfm: true,
});

export function renderMarkdown(text: string): string {
  const html = marked.parse(text, { async: false });
  return DOMPurify.sanitize(html, {
    // An allowlist, so a tag has to be named here to survive rather than merely not be blocked.
    ALLOWED_TAGS: [
      'p', 'br', 'strong', 'em', 'del', 'code', 'pre', 'blockquote',
      'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
      'a', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'hr',
    ],
    ALLOWED_ATTR: ['href', 'title'],
    // Only these schemes on a link. Blocks `javascript:` and `data:` URLs.
    ALLOWED_URI_REGEXP: /^(?:https?|mailto):/i,
  });
}
