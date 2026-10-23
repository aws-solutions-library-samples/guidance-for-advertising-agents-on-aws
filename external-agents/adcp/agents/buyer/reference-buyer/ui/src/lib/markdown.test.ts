import { describe, expect, it } from 'vitest';

import { renderMarkdown } from './markdown';

describe('renderMarkdown', () => {
  it('renders ordinary markdown', () => {
    const html = renderMarkdown('**bold** and `code`');
    expect(html).toContain('<strong>bold</strong>');
    expect(html).toContain('<code>code</code>');
  });

  it('keeps single newlines as breaks, which is what model output means by them', () => {
    expect(renderMarkdown('one\ntwo')).toContain('<br>');
  });

  it('renders lists and tables, which replies use for inventory', () => {
    expect(renderMarkdown('- a\n- b')).toContain('<li>');
    expect(renderMarkdown('| a | b |\n| --- | --- |\n| 1 | 2 |')).toContain('<table>');
  });

  it('strips a script tag', () => {
    const html = renderMarkdown('hi <script>alert(1)</script>');
    expect(html).not.toContain('<script');
    expect(html).not.toContain('alert(1)');
  });

  it('strips an event handler attribute', () => {
    expect(renderMarkdown('<p onclick="steal()">x</p>')).not.toContain('onclick');
  });

  it('drops a javascript: link while keeping the text', () => {
    const html = renderMarkdown('[click](javascript:alert(1))');
    expect(html).not.toContain('javascript:');
    expect(html).toContain('click');
  });

  it('drops a data: URL link', () => {
    expect(renderMarkdown('[x](data:text/html;base64,PHNjcmlwdD4=)')).not.toContain('data:');
  });

  it('keeps an ordinary https link', () => {
    expect(renderMarkdown('[docs](https://example.com)')).toContain('href="https://example.com"');
  });

  it('strips an iframe, which is not on the allowlist', () => {
    expect(renderMarkdown('<iframe src="https://evil.test"></iframe>')).not.toContain('<iframe');
  });

  it('strips an image, so a reply cannot make the browser fetch a tracker', () => {
    // Not on the allowlist: replies have no reason to embed remote images, and one would leak a
    // page view to whoever supplied the URL.
    expect(renderMarkdown('![x](https://tracker.test/p.gif)')).not.toContain('<img');
  });

  it('handles empty text', () => {
    expect(renderMarkdown('')).toBe('');
  });
});
