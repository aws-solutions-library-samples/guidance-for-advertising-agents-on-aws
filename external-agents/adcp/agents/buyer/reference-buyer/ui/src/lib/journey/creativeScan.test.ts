/**
 * The Creative scan panel's decoders, which had no tests at all.
 *
 * `decodeCreativeEvaluation` is what turns a `get_creative_features` response into the verdict that
 * makes `ScanBody` animate, and `decodeCreativeCards` attaches it to a format card. Both were written,
 * wired into `derive.ts`, and never covered — which mattered more than it looks: the deployed evaluator
 * was returning a body that failed its own schema (`oneOf composition failed`), so in every real session
 * this decoder saw an error response, returned null, and the panel showed cards with no verdict and no
 * scan. Untested decoder plus broken producer, and nothing on either side said so.
 *
 * The success payload below is the REAL response captured from the deployed governance agent after that
 * was fixed (session adcp-buyer-agent-e2537a14, read back from the sessions table), not an invented
 * shape. If the agent's output drifts from what this decoder reads, these fail.
 */

import { describe, expect, it } from 'vitest';

import { evaluatedFormatId } from './derive';
import { decodeCreativeCards, decodeCreativeEvaluation } from './governanceCards';
import type { ToolExchange } from './types';

const FORMAT = 'display_300x250';

/** The real evaluator response, verbatim from the deployed agent. */
const LIVE_RESULTS = [
  {
    feature_id: 'dimension_conformance',
    value: true,
    confidence: 1.0,
    measured_at: '2026-09-01T21:56:17.813719Z',
    details: {
      measured_width: 300,
      measured_height: 250,
      format_required_width: 300,
      format_required_height: 250,
    },
  },
  {
    feature_id: 'contrast_ratio',
    value: 4.87,
    unit: 'ratio',
    confidence: 0.7,
    measured_at: '2026-09-01T21:56:17.813719Z',
    details: {
      methodology:
        "WCAG 2.x relative luminance contrast between the image's two most frequent colors (foreground/background approximation for a flat-color banner)",
      wcag_aa_threshold: 4.5,
    },
  },
  {
    feature_id: 'urgency_claim',
    value: false,
    confidence: 0.85,
    measured_at: '2026-09-01T21:56:17.813719Z',
    methodology_version: 'ocr-tesseract-v1',
  },
];

function evaluatorExchange(output: unknown, input: unknown = { format_id: FORMAT }): ToolExchange {
  return {
    callId: 'tooluse_creative',
    toolName: 'adcp_get_creative_features',
    input,
    callIndex: 4,
    result: { output, status: 'success', index: 5 },
  };
}

describe('decodeCreativeEvaluation', () => {
  it('reads a verdict out of the real deployed response', () => {
    const evaluation = decodeCreativeEvaluation(
      evaluatorExchange({ status: 'completed', results: LIVE_RESULTS }),
      FORMAT,
    );
    expect(evaluation).not.toBeNull();
    expect(evaluation?.formatId).toBe(FORMAT);
    expect(evaluation?.clean).toBe(true);
  });

  it('reports the contrast ratio as its real number, not a pass/fail collapse', () => {
    // A specific figure is more use on screen than a second boolean, and it is a real measurement.
    const evaluation = decodeCreativeEvaluation(
      evaluatorExchange({ status: 'completed', results: LIVE_RESULTS }),
      FORMAT,
    );
    expect(evaluation?.features).toContain('contrast 4.87');
  });

  it('does not list a boolean feature that is false', () => {
    // `urgency_claim: false` is a real absence-of-detection. Listing it would read as a finding.
    const evaluation = decodeCreativeEvaluation(
      evaluatorExchange({ status: 'completed', results: LIVE_RESULTS }),
      FORMAT,
    );
    expect(evaluation?.features).not.toContain('urgency_claim');
  });

  it('is not clean when the asset does not match the format dimensions', () => {
    const evaluation = decodeCreativeEvaluation(
      evaluatorExchange({
        status: 'completed',
        results: [{ feature_id: 'dimension_conformance', value: false, confidence: 1.0 }],
      }),
      FORMAT,
    );
    expect(evaluation?.clean).toBe(false);
  });

  it('is not clean when a real urgency claim is detected', () => {
    const evaluation = decodeCreativeEvaluation(
      evaluatorExchange({
        status: 'completed',
        results: [{ feature_id: 'urgency_claim', value: true, confidence: 0.85 }],
      }),
      FORMAT,
    );
    expect(evaluation?.clean).toBe(false);
    expect(evaluation?.features).toContain('urgency_claim');
  });

  it('returns no verdict for the error variant', () => {
    // The state the panel was actually in for every real session before the evaluator was fixed.
    const evaluation = decodeCreativeEvaluation(
      evaluatorExchange({
        status: 'completed',
        errors: [{ code: 'UNSUPPORTED_FEATURE', message: 'could not fetch creative asset' }],
      }),
      FORMAT,
    );
    expect(evaluation).toBeNull();
  });

  it('returns no verdict when results is absent', () => {
    expect(decodeCreativeEvaluation(evaluatorExchange({ status: 'completed' }), FORMAT)).toBeNull();
  });

  it('returns no verdict for a non-terminal status', () => {
    // Not hypothetical. AdCP's creative-governance page describes a real `working` path for slow
    // evaluation (its example is sandboxed malware scanning), with results delivered by webhook. This
    // project's own agent does not take it, but a third-party evaluator would, and treating an
    // unfinished evaluation as a verdict is the one outcome this panel must never produce.
    for (const status of ['working', 'submitted']) {
      expect(decodeCreativeEvaluation(evaluatorExchange({ status }), FORMAT)).toBeNull();
    }
  });

  it('returns no verdict when there is no output at all', () => {
    expect(decodeCreativeEvaluation(evaluatorExchange(null), FORMAT)).toBeNull();
  });
});

describe('decodeCreativeCards', () => {
  /**
   * A `list_creative_formats` exchange. Wrapped in the seller FAN-OUT envelope
   * (`{results: [{seller_id, response}]}`) because that is what `parseFanOut` reads — a bare
   * `{formats: [...]}` body decodes to null, since every seller-facing tool in this buyer fans out even
   * when it is scoped to one seller.
   */
  function formatsExchange(formatIds: readonly string[]): ToolExchange {
    return {
      callId: 'tooluse_formats',
      toolName: 'adcp_list_creative_formats',
      input: { seller_ids: ['reference'] },
      callIndex: 3,
      result: {
        index: 4,
        status: 'success',
        output: {
          results: [
            {
              seller_id: 'reference',
              seller_name: 'AdCP Reference Test Seller (sandbox, MCP)',
              response: {
                formats: formatIds.map((id) => ({
                  format_id: { id, agent_url: 'https://reference-seller.example/adcp/mcp' },
                  name: id,
                })),
              },
            },
          ],
        },
      },
    };
  }

  it('attaches the verdict to the format it was evaluated for', () => {
    const evaluation = decodeCreativeEvaluation(
      evaluatorExchange({ status: 'completed', results: LIVE_RESULTS }),
      FORMAT,
    );
    const cards = decodeCreativeCards(formatsExchange([FORMAT]), evaluation);
    expect(cards).not.toBeNull();
    const card = cards?.find((c) => c.format === FORMAT);
    expect(card).toBeDefined();
    expect(card?.clean).toBe(true);
  });

  it('leaves a sibling format with no verdict rather than borrowing one', () => {
    // The whole of S22: only the format that was actually evaluated gets a scan. A borrowed verdict
    // would assert a measurement that was never taken on that asset.
    const evaluation = decodeCreativeEvaluation(
      evaluatorExchange({ status: 'completed', results: LIVE_RESULTS }),
      FORMAT,
    );
    const cards = decodeCreativeCards(formatsExchange([FORMAT, 'video_16x9_30s']), evaluation);
    const other = cards?.find((c) => c.format === "video_16x9_30s");
    expect(other).toBeDefined();
    expect(other?.clean).toBeNull();
    expect(other?.features).toEqual([]);
  });

  it('renders cards with no verdict at all when the evaluator never ran', () => {
    const cards = decodeCreativeCards(formatsExchange([FORMAT]), null);
    expect(cards).not.toBeNull();
    expect(cards?.every((c) => c.clean === null)).toBe(true);
  });
});


/**
 * Which format an evaluation was about.
 *
 * This used to be the literal `'display_300x250'` in `decodeCreative`, on the reasoning that it is the
 * only format with a published fixture asset. True today, and still the wrong thing to assume: the
 * verdict would attach to that card whatever was really submitted, so evaluating any other format would
 * file a real measurement against an asset it never looked at. The request names the format.
 */
describe('evaluatedFormatId', () => {
  it('reads the format id the request names', () => {
    expect(evaluatedFormatId(evaluatorExchange({}, { format_id: 'video_16x9_30s' }))).toBe(
      'video_16x9_30s',
    );
  });

  it('reads a manifest-shaped {id, agent_url} reference too', () => {
    // The form AdCP uses for a format reference elsewhere, so a caller could reasonably pass it.
    const exchange = evaluatorExchange(
      {},
      { format_id: { id: 'audio_30s', agent_url: 'https://reference-seller.example/adcp/mcp' } },
    );
    expect(evaluatedFormatId(exchange)).toBe('audio_30s');
  });

  it('returns null when the request names no format', () => {
    // The caller then attaches no verdict: a scan result whose subject is unknown cannot be shown
    // against a card, and guessing one is how a real measurement gets filed against the wrong asset.
    expect(evaluatedFormatId(evaluatorExchange({}, { asset_url: 'https://x.test/a.png' }))).toBeNull();
  });

  it('returns null for an empty or non-string format id', () => {
    expect(evaluatedFormatId(evaluatorExchange({}, { format_id: '' }))).toBeNull();
    expect(evaluatedFormatId(evaluatorExchange({}, { format_id: 42 }))).toBeNull();
    expect(evaluatedFormatId(evaluatorExchange({}, { format_id: { agent_url: 'https://x.test' } }))).toBeNull();
  });

  it('returns null when there is no request at all', () => {
    expect(evaluatedFormatId(evaluatorExchange({}, null))).toBeNull();
    // Built inline rather than via the helper: passing `undefined` there would select the helper's own
    // default and quietly test the happy path instead. (It did, on the first run of this test.)
    const noInput: ToolExchange = {
      callId: 'tooluse_creative',
      toolName: 'adcp_get_creative_features',
      input: undefined,
      callIndex: 4,
      result: { output: {}, status: 'success', index: 5 },
    };
    expect(evaluatedFormatId(noInput)).toBeNull();
  });

  it('composes with decodeCreativeCards to file the verdict against the named format', () => {
    // What `decodeCreative` does, in the two steps it does it in — so a verdict for a non-display
    // format lands on that format's card and not on display's.
    const evaluator = evaluatorExchange(
      { status: 'completed', results: LIVE_RESULTS },
      { format_id: 'video_16x9_30s' },
    );
    const formatId = evaluatedFormatId(evaluator);
    expect(formatId).toBe('video_16x9_30s');

    const evaluation = decodeCreativeEvaluation(evaluator, formatId!);
    const cards = decodeCreativeCards(
      {
        callId: 'tooluse_formats',
        toolName: 'adcp_list_creative_formats',
        input: {},
        callIndex: 1,
        result: {
          index: 2,
          status: 'success',
          output: {
            results: [
              {
                seller_id: 'reference',
                response: {
                  formats: ['display_300x250', 'video_16x9_30s'].map((id) => ({
                    format_id: { id, agent_url: 'https://reference-seller.example/adcp/mcp' },
                    name: id,
                  })),
                },
              },
            ],
          },
        },
      },
      evaluation,
    );

    expect(cards?.find((c) => c.format === 'video_16x9_30s')?.clean).toBe(true);
    expect(cards?.find((c) => c.format === 'display_300x250')?.clean).toBeNull();
  });
});
