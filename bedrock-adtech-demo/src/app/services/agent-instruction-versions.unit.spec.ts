/**
 * Unit tests for instruction version history in AgentDynamoDBService.
 *
 * Exercises the real service methods against an in-memory DynamoDB stand-in, so
 * the sort-key scheme, revision numbering, and pointer/snapshot fan-out are
 * covered without touching a deployed table.
 */

// The service under test is an @Injectable, so importing it drags in Angular's
// ESM bundle, which this project's Jest setup does not transform. Only the
// decorator is needed at runtime, so stub the module rather than adding a
// repo-wide transform config. AwsConfigService is stubbed for the same reason;
// the table handle is injected directly in makeService().
jest.mock('@angular/core', () => ({
  Injectable: () => (target: unknown) => target
}));
jest.mock('./aws-config.service', () => ({ AwsConfigService: class {} }));

import {
  GetItemCommand,
  PutItemCommand,
  DeleteItemCommand,
  QueryCommand
} from '@aws-sdk/client-dynamodb';
import { marshall, unmarshall } from '@aws-sdk/util-dynamodb';

import {
  AgentDynamoDBService,
  INSTRUCTION_LIVE_SK,
  INSTRUCTION_VERSION_SK_PREFIX
} from './agent-dynamodb.service';

const TABLE = 'test-AgentConfig-unit';
const AGENT = 'AdFabricAgent';
const PK = `INSTRUCTION#${AGENT}`;

interface RecordedQuery {
  projection?: string;
  names?: Record<string, string>;
}

/**
 * Minimal DynamoDB stand-in over a Map, faithful to the parts this feature
 * relies on: composite-key storage, prefix queries with descending sort and
 * limit, and ProjectionExpression (so a query that wrongly fetched `content`
 * would show up here).
 */
class FakeDynamo {
  items = new Map<string, Record<string, any>>();
  queries: RecordedQuery[] = [];
  putCount = 0;
  deletedKeys: string[] = [];

  private key(pk: string, sk: string): string {
    return `${pk}||${sk}`;
  }

  seed(item: Record<string, any>): void {
    this.items.set(this.key(item['pk'], item['sk']), { ...item });
  }

  get(pk: string, sk: string): Record<string, any> | undefined {
    return this.items.get(this.key(pk, sk));
  }

  private applyProjection(
    item: Record<string, any>,
    projection?: string,
    names?: Record<string, string>
  ): Record<string, any> {
    if (!projection) return item;
    const wanted = projection.split(',')
      .map(token => token.trim())
      .map(token => (names && names[token]) || token);
    const out: Record<string, any> = {};
    for (const attr of wanted) {
      if (item[attr] !== undefined) out[attr] = item[attr];
    }
    return out;
  }

  async send(command: any): Promise<any> {
    if (command instanceof PutItemCommand) {
      this.putCount += 1;
      const item = unmarshall(command.input.Item as any);
      this.items.set(this.key(item['pk'], item['sk']), item);
      return {};
    }

    if (command instanceof GetItemCommand) {
      const key = unmarshall(command.input.Key as any);
      const found = this.items.get(this.key(key['pk'], key['sk']));
      if (!found) return {};
      const projected = this.applyProjection(
        found,
        command.input.ProjectionExpression,
        command.input.ExpressionAttributeNames
      );
      return { Item: marshall(projected, { removeUndefinedValues: true }) };
    }

    if (command instanceof DeleteItemCommand) {
      const key = unmarshall(command.input.Key as any);
      this.deletedKeys.push(this.key(key['pk'], key['sk']));
      this.items.delete(this.key(key['pk'], key['sk']));
      return {};
    }

    if (command instanceof QueryCommand) {
      const values = unmarshall(command.input.ExpressionAttributeValues as any);
      this.queries.push({
        projection: command.input.ProjectionExpression,
        names: command.input.ExpressionAttributeNames
      });

      // The GSI query path (visualization templates) is not what these tests
      // cover; report no matches so deleteAgent proceeds.
      if (command.input.IndexName) return { Items: [] };

      let matches = [...this.items.values()].filter(item => item['pk'] === values[':pk']);
      if (values[':prefix']) {
        matches = matches.filter(item => String(item['sk']).startsWith(values[':prefix']));
      }
      matches.sort((a, b) => String(a['sk']).localeCompare(String(b['sk'])));
      if (command.input.ScanIndexForward === false) matches.reverse();
      if (command.input.Limit) matches = matches.slice(0, command.input.Limit);

      return {
        Items: matches.map(item => marshall(
          this.applyProjection(item, command.input.ProjectionExpression, command.input.ExpressionAttributeNames),
          { removeUndefinedValues: true }
        ))
      };
    }

    throw new Error(`FakeDynamo: unsupported command ${command?.constructor?.name}`);
  }
}

function makeService(fake: FakeDynamo): AgentDynamoDBService {
  const service = new AgentDynamoDBService({} as any);
  const internals = service as any;
  internals.dynamoDBClient = fake;
  internals.tableName = TABLE;
  // Bypass Cognito credential resolution; the table handle is already injected.
  internals.ensureClient = async () => true;
  return service;
}

/** A live pointer written by the deploy path: content but no version attribute. */
function seedUnversionedLive(fake: FakeDynamo, content: string): void {
  fake.seed({
    pk: PK,
    sk: INSTRUCTION_LIVE_SK,
    config_type: 'instruction',
    agent_name: AGENT,
    content,
    updated_at: '2026-01-01T00:00:00.000Z'
  });
}

function versionSk(n: number): string {
  return `${INSTRUCTION_VERSION_SK_PREFIX}${String(n).padStart(6, '0')}`;
}

describe('AgentDynamoDBService instruction versions', () => {
  let fake: FakeDynamo;
  let service: AgentDynamoDBService;

  beforeEach(() => {
    fake = new FakeDynamo();
    service = makeService(fake);
  });

  describe('first save over a deploy-seeded record', () => {
    it('archives the existing text before overwriting the pointer', async () => {
      seedUnversionedLive(fake, 'ORIGINAL from the .txt library');

      const ok = await service.saveAgentInstructions(AGENT, 'EDITED in the UI', { author: 'zellest@amazon.com' });

      expect(ok).toBe(true);
      // The deploy-seeded prompt survives as v1 and is still recoverable.
      expect(fake.get(PK, versionSk(1))!['content']).toBe('ORIGINAL from the .txt library');
      expect(fake.get(PK, versionSk(2))!['content']).toBe('EDITED in the UI');
    });

    it('carries the original updated_at onto the archived snapshot', async () => {
      seedUnversionedLive(fake, 'ORIGINAL');
      await service.saveAgentInstructions(AGENT, 'EDITED');
      expect(fake.get(PK, versionSk(1))!['updated_at']).toBe('2026-01-01T00:00:00.000Z');
    });

    it('points the live record the runtime reads at the new text and version', async () => {
      seedUnversionedLive(fake, 'ORIGINAL');
      await service.saveAgentInstructions(AGENT, 'EDITED');

      const live = fake.get(PK, INSTRUCTION_LIVE_SK)!;
      expect(live['content']).toBe('EDITED');
      expect(live['instruction_version']).toBe(2);
      expect(live['config_type']).toBe('instruction');
    });

    it('starts at v1 when no live record exists at all', async () => {
      await service.saveAgentInstructions(AGENT, 'BRAND NEW');
      expect(fake.get(PK, versionSk(1))!['content']).toBe('BRAND NEW');
      expect(fake.get(PK, INSTRUCTION_LIVE_SK)!['instruction_version']).toBe(1);
    });
  });

  describe('subsequent saves', () => {
    it('appends one version per change without re-archiving', async () => {
      seedUnversionedLive(fake, 'ORIGINAL');
      await service.saveAgentInstructions(AGENT, 'SECOND');
      await service.saveAgentInstructions(AGENT, 'THIRD');
      await service.saveAgentInstructions(AGENT, 'FOURTH');

      expect(fake.get(PK, versionSk(1))!['content']).toBe('ORIGINAL');
      expect(fake.get(PK, versionSk(2))!['content']).toBe('SECOND');
      expect(fake.get(PK, versionSk(3))!['content']).toBe('THIRD');
      expect(fake.get(PK, versionSk(4))!['content']).toBe('FOURTH');
      expect(fake.get(PK, versionSk(5))).toBeUndefined();
    });

    it('does not add a version when the text is unchanged', async () => {
      seedUnversionedLive(fake, 'ORIGINAL');
      await service.saveAgentInstructions(AGENT, 'SECOND');
      const writesAfterRealChange = fake.putCount;

      const ok = await service.saveAgentInstructions(AGENT, 'SECOND');

      expect(ok).toBe(true);
      expect(fake.putCount).toBe(writesAfterRealChange);
      expect(fake.get(PK, versionSk(3))).toBeUndefined();
    });

    it('records the author when supplied and omits it when not', async () => {
      await service.saveAgentInstructions(AGENT, 'ONE', { author: 'someone@example.com' });
      await service.saveAgentInstructions(AGENT, 'TWO');

      expect(fake.get(PK, versionSk(1))!['author']).toBe('someone@example.com');
      expect(fake.get(PK, versionSk(2))!['author']).toBeUndefined();
    });
  });

  describe('version numbering past a decimal boundary', () => {
    it('orders v10 after v9 rather than lexicographically', async () => {
      seedUnversionedLive(fake, 'live');
      for (let i = 1; i <= 9; i++) {
        fake.seed({
          pk: PK, sk: versionSk(i), config_type: 'instruction_version',
          agent_name: AGENT, content: `body ${i}`, instruction_version: i,
          updated_at: `2026-02-0${i}T00:00:00.000Z`
        });
      }
      // Live is unversioned, so this save archives it then publishes: 10, then 11.
      await service.saveAgentInstructions(AGENT, 'eleventh');

      expect(fake.get(PK, versionSk(10))!['content']).toBe('live');
      expect(fake.get(PK, versionSk(11))!['content']).toBe('eleventh');

      const history = await service.getInstructionVersionHistory(AGENT);
      expect(history.versions.map(v => v.version)).toEqual([11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]);
    });
  });

  describe('getInstructionVersionHistory', () => {
    it('returns snapshots newest first with their metadata', async () => {
      await service.saveAgentInstructions(AGENT, 'ONE', { author: 'a@example.com' });
      await service.saveAgentInstructions(AGENT, 'TWO', { author: 'b@example.com' });

      const history = await service.getInstructionVersionHistory(AGENT);

      expect(history.versions.map(v => v.version)).toEqual([2, 1]);
      expect(history.versions[0].author).toBe('b@example.com');
      expect(history.versions[0].contentLength).toBe('TWO'.length);
      expect(history.versions[0].sk).toBe(versionSk(2));
    });

    it('does not fetch instruction bodies when listing', async () => {
      await service.saveAgentInstructions(AGENT, 'a fairly long instruction body');
      fake.queries = [];

      await service.getInstructionVersionHistory(AGENT);

      const listQuery = fake.queries.find(q => q.projection);
      expect(listQuery).toBeDefined();
      // Pulling every historical prompt into the browser is what the projection
      // exists to avoid.
      expect(listQuery!.projection).not.toContain('content');
      expect(Object.values(listQuery!.names || {})).not.toContain('content');
    });

    it('reports the live version when the pointer was published through the UI', async () => {
      await service.saveAgentInstructions(AGENT, 'ONE');
      const history = await service.getInstructionVersionHistory(AGENT);
      expect(history.live.exists).toBe(true);
      expect(history.live.version).toBe(1);
    });

    it('reports a null live version for a deploy-written pointer', async () => {
      seedUnversionedLive(fake, 'from deploy');
      const history = await service.getInstructionVersionHistory(AGENT);
      expect(history.live.exists).toBe(true);
      // Null is what drives the "not versioned" label instead of claiming a version.
      expect(history.live.version).toBeNull();
      expect(history.versions).toEqual([]);
    });

    it('returns an empty history for an agent with no instructions', async () => {
      const history = await service.getInstructionVersionHistory(AGENT);
      expect(history.versions).toEqual([]);
      expect(history.live.exists).toBe(false);
      expect(history.live.version).toBeNull();
    });
  });

  describe('reading a specific version', () => {
    it('returns the archived text for a snapshot key', async () => {
      seedUnversionedLive(fake, 'ORIGINAL');
      await service.saveAgentInstructions(AGENT, 'REPLACEMENT');

      const restored = await service.getAgentInstructionsAtVersion(AGENT, versionSk(1));
      expect(restored).toBe('ORIGINAL');
    });

    it('returns the running text for the live key', async () => {
      seedUnversionedLive(fake, 'ORIGINAL');
      await service.saveAgentInstructions(AGENT, 'REPLACEMENT');

      const live = await service.getAgentInstructionsAtVersion(AGENT, INSTRUCTION_LIVE_SK);
      expect(live).toBe('REPLACEMENT');
    });

    it('returns null for a version that does not exist', async () => {
      const missing = await service.getAgentInstructionsAtVersion(AGENT, versionSk(99));
      expect(missing).toBeNull();
    });

    it('publishing a restored version appends rather than rewinding', async () => {
      seedUnversionedLive(fake, 'ORIGINAL');
      await service.saveAgentInstructions(AGENT, 'REPLACEMENT');
      const restored = (await service.getAgentInstructionsAtVersion(AGENT, versionSk(1)))!;

      await service.saveAgentInstructions(AGENT, restored);

      // v1 and v2 are intact; the restore became v3.
      expect(fake.get(PK, versionSk(1))!['content']).toBe('ORIGINAL');
      expect(fake.get(PK, versionSk(2))!['content']).toBe('REPLACEMENT');
      expect(fake.get(PK, versionSk(3))!['content']).toBe('ORIGINAL');
      expect(fake.get(PK, INSTRUCTION_LIVE_SK)!['instruction_version']).toBe(3);
    });
  });

  describe('deleteAgent', () => {
    it('removes every version snapshot along with the live record', async () => {
      fake.seed({
        pk: 'GLOBAL_CONFIG', sk: 'v1', config_type: 'global_config',
        content: JSON.stringify({
          knowledge_bases: {}, configured_colors: {},
          agent_configs: { [AGENT]: { agent_name: AGENT } }
        })
      });
      await service.saveAgentInstructions(AGENT, 'ONE');
      await service.saveAgentInstructions(AGENT, 'TWO');
      await service.saveAgentInstructions(AGENT, 'THREE');
      expect(fake.get(PK, versionSk(3))).toBeDefined();

      await service.deleteAgent(AGENT);

      // Nothing under the instruction partition should survive, otherwise a new
      // agent of the same name inherits a stranger's prompt history.
      const leftover = [...fake.items.values()].filter(item => item['pk'] === PK);
      expect(leftover).toEqual([]);
    });
  });
});
