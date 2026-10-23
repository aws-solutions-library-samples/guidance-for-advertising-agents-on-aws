/**
 * Channels and formats are different vocabularies. This module exists because they were being confused.
 *
 * They overlap in spelling — both contain something that reads as "audio", "display", "video" — and
 * nothing in the type system distinguishes a channel string from a format string. That is how a chip
 * ended up reading "channel: audio" when **AdCP has no `audio` channel**. Its channel enum says
 * `streaming_audio`.
 *
 * ## The distinction, stated once
 *
 * **CHANNEL** — *where* the advertising runs. A closed protocol enum, fixed by AdCP, used on a campaign
 * plan's `channels.allowed` and validated by the SDK. Send a value outside it and `sync_plans` is
 * rejected; the reference governance agent's own tests prove that. Twenty values, listed below.
 *
 * **FORMAT** — *what the creative is*. Agent-defined and **deliberately open**: a product carries
 * `format_ids: [{ id: "audio_30s", agent_url }]`, and `audio_30s` is that seller's label, not a protocol
 * constant. AdCP is explicit that agent-defined identifiers must not be pattern-matched as though they
 * were enums.
 *
 * So the two are not merely different lists, they are different *kinds* of thing: one is closed and
 * validated, the other is open and belongs to whoever published it. A single "audio" constant serving
 * both is wrong twice over.
 *
 * ## What each is allowed to be used for
 *
 * | | Channel | Format |
 * |---|---|---|
 * | Source | AdCP enum | a seller's `format_ids` |
 * | Closed? | yes, validated | no, open |
 * | Used by | the `channel` chip, and `sync_plans` in future | Discover's format groups |
 * | May be matched against a fixed list? | yes, that is what an enum is for | **no** |
 *
 * The format side keeps its existing home in `phases.ts`'s `FORMAT_META`, because those five keys are
 * the prototype's authored *display groupings* — a presentation choice about how to bucket whatever a
 * seller returns, not a claim about a protocol vocabulary. Grouping `audio_30s` under a group labelled
 * "Streaming audio" is accurate; calling `audio` a channel is not.
 */

/**
 * AdCP's channel enum, read from the SDK's own `Plan.channels.allowed` type.
 *
 * A closed set. `vocabulary.test.ts` compares this list against the SDK's generated types so it cannot
 * drift from the protocol without failing.
 */
export const ADCP_CHANNELS = [
  'affiliate',
  'cinema',
  'ctv',
  'display',
  'dooh',
  'email',
  'gaming',
  'influencer',
  'linear_tv',
  'olv',
  'ooh',
  'podcast',
  'print',
  'product_placement',
  'radio',
  'retail_media',
  'search',
  'social',
  'sponsored_intelligence',
  'streaming_audio',
] as const;

export type AdcpChannel = (typeof ADCP_CHANNELS)[number];

const CHANNEL_SET: ReadonlySet<string> = new Set(ADCP_CHANNELS);

export function isAdcpChannel(value: string): value is AdcpChannel {
  return CHANNEL_SET.has(value);
}

/**
 * Everyday words that mean an AdCP channel, mapped to the channel they mean.
 *
 * A brief is prose written by a person; it says "audio" and "newsletter", not `streaming_audio` and
 * `email`. So spoken words are recognised and **translated to the protocol value**, rather than the
 * spoken word being passed along as though it were one.
 *
 * That translation is the fix for a real latent failure: the `channel` chip previously reported the word
 * it matched. "audio" would then have flowed into `sync_plans` in U2 and been rejected by the enum,
 * which is a validation error surfacing one unit later than the mistake.
 *
 * Longest key first at match time, so "streaming audio" wins over "audio" and resolves to one channel
 * rather than two.
 */
export const CHANNEL_SYNONYMS: Readonly<Record<string, AdcpChannel>> = {
  'streaming audio': 'streaming_audio',
  streaming_audio: 'streaming_audio',
  audio: 'streaming_audio',
  podcast: 'podcast',
  radio: 'radio',
  display: 'display',
  banner: 'display',
  video: 'olv',
  'online video': 'olv',
  olv: 'olv',
  preroll: 'olv',
  'pre-roll': 'olv',
  ctv: 'ctv',
  'connected tv': 'ctv',
  'linear tv': 'linear_tv',
  linear_tv: 'linear_tv',
  newsletter: 'email',
  email: 'email',
  social: 'social',
  search: 'search',
  cinema: 'cinema',
  print: 'print',
  dooh: 'dooh',
  ooh: 'ooh',
  'out of home': 'ooh',
  gaming: 'gaming',
  'retail media': 'retail_media',
  retail_media: 'retail_media',
  influencer: 'influencer',
  affiliate: 'affiliate',
};

/**
 * Channels named in a piece of prose, as protocol values, in the order they appear.
 *
 * Returns `[]` rather than a guess when nothing matches: a brief that names no channel has no channel,
 * and the caller omits the chip.
 *
 * `native` is deliberately absent from the synonym table. It is a real advertising word and **not an
 * AdCP channel**, so a brief saying "native" produces no channel rather than being bent into the nearest
 * enum value. Mapping it to `display` would be a guess dressed as a translation.
 */
export function channelsInText(text: string): AdcpChannel[] {
  const haystack = text.toLowerCase();
  // Longest first, so a multi-word synonym is not shadowed by a substring of itself.
  const keys = Object.keys(CHANNEL_SYNONYMS).sort((a, b) => b.length - a.length);

  const found: AdcpChannel[] = [];
  const consumed: Array<[number, number]> = [];

  for (const key of keys) {
    let from = 0;
    for (;;) {
      const at = haystack.indexOf(key, from);
      if (at === -1) break;
      from = at + key.length;
      // Skip a hit inside a span a longer synonym already claimed, so "streaming audio" does not also
      // register "audio".
      if (consumed.some(([start, end]) => at >= start && at < end)) continue;
      consumed.push([at, at + key.length]);
      const channel = CHANNEL_SYNONYMS[key];
      if (channel !== undefined && !found.includes(channel)) found.push(channel);
    }
  }
  return found;
}

/**
 * How a set of channels reads on screen.
 *
 * The protocol value is what travels; a person still has to read it. `streaming_audio` becomes
 * "streaming audio" — the underscore removed and nothing else changed, so the label and the value cannot
 * disagree about which channel is meant.
 */
export function describeChannels(channels: readonly AdcpChannel[]): string {
  return channels.map((channel) => channel.replace(/_/g, ' ')).join(' + ');
}
