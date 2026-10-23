/**
 * The prototype's demo names must not appear in shipped source.
 *
 * The prototype hardcoded "Triton", "Boltive" and "Clean Rooms" into its headline and panel copy. Each
 * was a claim about a specific party in a session that might involve none of them, and the headline in
 * particular put the least verified statement on the screen at the largest size.
 *
 * A per-string assertion cannot catch the next occurrence, so this scans every non-test source file.
 * Test files are excluded because asserting on the authored string is exactly what they are for.
 *
 * This scan used to live in `headline.test.ts`. That file went away with `buildHeadline` when the
 * header became static, and the scan is the part worth keeping: it guards the whole tree rather than
 * one module, and the defect it covers is not specific to the headline.
 */

import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const here = dirname(fileURLToPath(import.meta.url));

describe('the prototype\u2019s demo names do not appear in shipped source', () => {
  const DEMO_NAMES = ['Triton', 'Boltive', 'Clean Rooms'];

  function sourceFiles(dir: string): string[] {
    const { readdirSync, statSync } = require('node:fs') as typeof import('node:fs');
    const out: string[] = [];
    for (const entry of readdirSync(dir)) {
      const full = resolve(dir, entry);
      if (statSync(full).isDirectory()) {
        out.push(...sourceFiles(full));
        continue;
      }
      if (!/\.(ts|tsx)$/.test(entry)) continue;
      // A test asserting on the authored string is the point, not a violation.
      if (/\.test\.tsx?$/.test(entry)) continue;
      out.push(full);
    }
    return out;
  }

  /**
   * Known occurrences, listed so a NEW one fails rather than being absorbed.
   *
   * All three are empty, and that is the current truth rather than an aspiration: the Book panel's
   * authored "Booked at Triton" was the last one, and it is now seller-neutral. Listing rather than
   * skipping is deliberate: a skip hides the next occurrence, an expected-set comparison surfaces it.
   */
  const KNOWN: Record<string, readonly string[]> = {
    Triton: [],
    Boltive: [],
    'Clean Rooms': [],
  };

  it('scans a real set of files, not an empty one', () => {
    // Without this the whole suite passes if the walk breaks or the path moves.
    const files = sourceFiles(resolve(here, '../..'));
    expect(files.length).toBeGreaterThan(20);
  });

  it.each(DEMO_NAMES)('%s appears only where it is already known and tracked', (name) => {
    const { readFileSync: read } = require('node:fs') as typeof import('node:fs');
    const offenders = sourceFiles(resolve(here, '../..'))
      .filter((file) => {
        const text = read(file, 'utf8');
        // A comment may legitimately explain why a name is absent, or record why it was removed.
        // Strip comments before judging.
        const code = text
          .replace(/\/\*[\s\S]*?\*\//g, '')
          .replace(/(^|[^:])\/\/.*$/gm, '$1');
        return code.includes(name);
      })
      .map((file) => file.split('/src/')[1])
      .sort();
    expect(offenders).toEqual([...(KNOWN[name] ?? [])].sort());
  });

  it('keeps the summary header and the view itself free of them', () => {
    const { readFileSync: read } = require('node:fs') as typeof import('node:fs');
    // The files where this defect actually occurred. Named explicitly so the regression is guarded by
    // something more direct than a whole-tree scan. `headline.ts` was one of them and no longer exists.
    const files = [
      resolve(here, '../../components/journey/CampaignSummary.tsx'),
      resolve(here, '../../components/journey/JourneyView.tsx'),
      resolve(here, 'phases.ts'),
    ];
    for (const file of files) {
      const code = read(file, 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/(^|[^:])\/\/.*$/gm, '$1');
      DEMO_NAMES.forEach((name) => expect(code).not.toContain(name));
    }
  });
});
