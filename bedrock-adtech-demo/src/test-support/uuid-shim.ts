// Jest-only stand-in for `uuid`. uuid 14 ships ESM only, which ts-jest (CommonJS)
// cannot load, so every spec importing a service that uses `v4` failed to start.
// The app build never includes this file; jest.config.js maps `uuid` here.
import { randomUUID } from 'crypto';

export const v4 = (): string => randomUUID();
