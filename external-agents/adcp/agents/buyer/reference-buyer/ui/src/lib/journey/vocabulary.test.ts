/**
 * Channels versus formats.
 *
 * The first test is the important one: it reads the **SDK's own generated type** and asserts our channel
 * list matches it. Everything else in this file guards the boundary between the two vocabularies, but
 * only that test can catch the protocol changing underneath us.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { FORMAT_KEYS } from './phases';
import {
  ADCP_CHANNELS,
  CHANNEL_SYNONYMS,
  channelsInText,
  describeChannels,
  isAdcpChannel,
} from './vocabulary';

const here = dirname(fileURLToPath(import.meta.url));

/**
 * The SDK's generated channel enum.
 *
 * `enums/channels.py`, not the plan type: the plan REFERENCES the enum rather than restating it, so
 * reading the plan file found no literals at all and the first version of this test failed for the right
 * reason. Reading the definition is what makes this a check against the protocol.
 */
const SDK_CHANNEL_ENUM = resolve(
  here,
  '../../../../../../governance/reference-governance/app/adcpRefGovernance/.venv/lib/python3.12/'
    + 'site-packages/adcp/types/generated_poc/enums/channels.py',
);

describe('the channel list matches the SDK', () => {
  it('reads the SDK-generated plan type', () => {
    // If this file moves the assertion below would pass vacuously on an empty match.
    const source = readFileSync(SDK_CHANNEL_ENUM, 'utf8');
    expect(source).toContain('streaming_audio');
  });

  it('contains exactly the channels the SDK enumerates', () => {
    const source = readFileSync(SDK_CHANNEL_ENUM, 'utf8');
    // The generated enum members appear as quoted literals in the Channel class.
    const fromSdk = new Set(
      [...source.matchAll(/^\s+[a-z_]+ = '([a-z_]+)'$/gm)].map((m) => m[1] as string),
    );
    // Restrict to the ones we claim, then assert the claim is complete in both directions.
    for (const channel of ADCP_CHANNELS) {
      expect(fromSdk.has(channel), `${channel} is not in the SDK enum`).toBe(true);
    }
    // And in the other direction: the SDK lists nothing we have missed. This is the half that catches
    // the protocol GAINING a channel, which a one-way check would not.
    expect([...fromSdk].sort()).toEqual([...ADCP_CHANNELS].sort());
  });
});

describe('channels and formats are not the same vocabulary', () => {
  it('does not treat a format group key as a channel', () => {
    // `audio`, `video` and `endcard` are format groupings, not channels. This is the confusion that put
    // "channel: audio" on screen when AdCP has no such channel.
    expect(isAdcpChannel('audio')).toBe(false);
    expect(isAdcpChannel('video')).toBe(false);
    expect(isAdcpChannel('endcard')).toBe(false);
  });

  it('has format keys that are mostly NOT channels, which is the point', () => {
    const alsoChannels = FORMAT_KEYS.filter((key) => isAdcpChannel(key));
    // `podcast` and `display` genuinely appear in both vocabularies; the rest do not. The overlap is
    // exactly why the two must be resolved separately rather than shared.
    expect([...alsoChannels].sort()).toEqual(['display', 'podcast']);
  });

  it('every synonym resolves to a real AdCP channel', () => {
    for (const [word, channel] of Object.entries(CHANNEL_SYNONYMS)) {
      expect(isAdcpChannel(channel), `${word} -> ${channel} is not an AdCP channel`).toBe(true);
    }
  });
});

describe('channelsInText', () => {
  it('translates a spoken word to the protocol value', () => {
    // The fix. A brief says "audio"; the plan needs `streaming_audio`.
    expect(channelsInText('Channel: Audio. Please return availability.')).toEqual([
      'streaming_audio',
    ]);
  });

  it('does not report the spoken word as though it were a channel', () => {
    expect(channelsInText('audio')).not.toContain('audio');
  });

  it('resolves the longer phrase, not its substring', () => {
    // "streaming audio" must not also register "audio" and yield two channels for one statement.
    expect(channelsInText('streaming audio only')).toEqual(['streaming_audio']);
  });

  it('maps everyday words onto the enum', () => {
    expect(channelsInText('video pre-roll')).toEqual(['olv']);
    expect(channelsInText('a newsletter placement')).toEqual(['email']);
    expect(channelsInText('connected TV')).toEqual(['ctv']);
    expect(channelsInText('out of home boards')).toEqual(['ooh']);
  });

  it('finds several channels and keeps them distinct', () => {
    const found = channelsInText('Podcast + radio, plus some display');
    expect(new Set(found)).toEqual(new Set(['podcast', 'radio', 'display']));
  });

  it('does not bend a real advertising word that AdCP has no channel for', () => {
    // "native" is a real word and not an AdCP channel. Mapping it to `display` would be a guess dressed
    // as a translation.
    expect(channelsInText('native placements please')).toEqual([]);
  });

  it('returns nothing rather than guessing when no channel is named', () => {
    expect(channelsInText('Reach adults 18-49 in December with a big idea.')).toEqual([]);
  });

  it('is case insensitive', () => {
    expect(channelsInText('STREAMING_AUDIO')).toEqual(['streaming_audio']);
    expect(channelsInText('Podcast')).toEqual(['podcast']);
  });
});

describe('describeChannels', () => {
  it('reads as prose without changing which channel is meant', () => {
    expect(describeChannels(['streaming_audio'])).toBe('streaming audio');
    expect(describeChannels(['streaming_audio', 'podcast'])).toBe('streaming audio + podcast');
  });

  it('is empty for no channels, so a caller renders no chip', () => {
    expect(describeChannels([])).toBe('');
  });
});
