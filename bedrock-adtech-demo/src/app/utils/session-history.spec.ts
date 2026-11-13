import { AgentEvents, MemoryEvent, eventsToHistory, toActorId } from './session-history';

let eventCounter = 0;

function textEvent(role: 'USER' | 'ASSISTANT', text: string, at: string): MemoryEvent {
  eventCounter += 1;
  return {
    eventId: `evt-${eventCounter}`,
    eventTimestamp: new Date(at),
    payload: [{ conversational: { role, content: { text } } }],
  };
}

/** One event holding several messages, as `add_turns` writes a whole turn. */
function turnEvent(at: string, messages: Array<['USER' | 'ASSISTANT', string]>): MemoryEvent {
  eventCounter += 1;
  return {
    eventId: `evt-${eventCounter}`,
    eventTimestamp: new Date(at),
    payload: messages.map(([role, text]) => ({ conversational: { role, content: { text } } })),
  };
}

function summarize(eventsByAgent: AgentEvents[]): string[] {
  return eventsToHistory(eventsByAgent).map(entry => `${entry.agentName}/${entry.role}: ${entry.text}`);
}

describe('session-history', () => {
  it('merges events from several agents oldest first', () => {
    const agency: AgentEvents = {
      agentName: 'AgencyAgent',
      // ListEvents does not promise an order, so these arrive newest first.
      events: [
        textEvent('ASSISTANT', 'Second answer', '2025-01-01T10:03:00Z'),
        textEvent('USER', 'Second question', '2025-01-01T10:02:30Z'),
        textEvent('ASSISTANT', 'First answer', '2025-01-01T10:00:30Z'),
        textEvent('USER', 'First question', '2025-01-01T10:00:00Z'),
      ],
    };
    const media: AgentEvents = {
      agentName: 'MediaPlanningAgent',
      events: [
        textEvent('USER', 'Media question', '2025-01-01T10:01:00Z'),
        textEvent('ASSISTANT', 'Media answer', '2025-01-01T10:01:30Z'),
      ],
    };

    const history = eventsToHistory([agency, media]);

    expect(history.map(entry => entry.text)).toEqual([
      'First question',
      'First answer',
      'Media question',
      'Media answer',
      'Second question',
      'Second answer',
    ]);
  });

  it('attributes user and assistant events to the right speaker', () => {
    const history = eventsToHistory([
      { agentName: 'Media_Planning_Agent', events: [textEvent('USER', 'Build a plan', '2025-01-01T09:00:00Z')] },
      { agentName: 'AgencyAgent', events: [textEvent('ASSISTANT', 'Here is the brief', '2025-01-01T09:00:01Z')] },
    ]);

    expect(history).toEqual([
      { role: 'user', agentName: 'Media_Planning_Agent', text: 'Build a plan', timestamp: new Date('2025-01-01T09:00:00Z') },
      { role: 'assistant', agentName: 'AgencyAgent', text: 'Here is the brief', timestamp: new Date('2025-01-01T09:00:01Z') },
    ]);
    expect(toActorId('Media_Planning_Agent')).toBe('Media-Planning-Agent');
  });

  it('strips the User Question prefix and the Context block', () => {
    const firstMessage = 'User Question: plan a campaign\n\nContext:{"publisher":{"name":"MagicStream"}}';
    const firstWithoutContext = 'User Question: and the budget?';
    const typedText = 'Compare Context: CTV versus display';

    expect(
      summarize([
        {
          agentName: 'AgencyAgent',
          events: [
            textEvent('USER', firstMessage, '2025-01-01T08:00:00Z'),
            textEvent('USER', firstWithoutContext, '2025-01-01T08:01:00Z'),
            textEvent('USER', typedText, '2025-01-01T08:02:00Z'),
          ],
        },
      ])
    ).toEqual([
      'AgencyAgent/user: plan a campaign',
      'AgencyAgent/user: and the budget?',
      'AgencyAgent/user: Compare Context: CTV versus display',
    ]);
  });

  it('drops an adjacent duplicate from the same agent', () => {
    const adjacent = summarize([
      {
        agentName: 'AgencyAgent',
        events: [
          textEvent('USER', 'Plan a campaign', '2025-01-01T07:00:00Z'),
          textEvent('USER', 'Plan a campaign', '2025-01-01T07:00:00Z'),
          textEvent('ASSISTANT', 'Here is the plan', '2025-01-01T07:00:20Z'),
        ],
      },
      // The same text from another agent is a different message.
      { agentName: 'MediaPlanningAgent', events: [textEvent('USER', 'Plan a campaign', '2025-01-01T07:00:40Z')] },
    ]);
    expect(adjacent).toEqual([
      'AgencyAgent/user: Plan a campaign',
      'AgencyAgent/assistant: Here is the plan',
      'MediaPlanningAgent/user: Plan a campaign',
    ]);

    // The old writers stored each message as it was added, then the whole turn
    // again (tool text included) once the turn ended.
    const rewrittenTurn = summarize([
      {
        agentName: 'AgencyAgent',
        events: [
          textEvent('USER', 'Plan a campaign', '2025-01-01T07:00:00Z'),
          textEvent('ASSISTANT', 'Here is the plan', '2025-01-01T07:00:20Z'),
          turnEvent('2025-01-01T07:00:21Z', [
            ['USER', 'Plan a campaign'],
            ['ASSISTANT', "{'toolUse': {'toolUseId': 'tooluse_1', 'name': 'lookup'}}"],
            ['USER', "{'toolResult': {'toolUseId': 'tooluse_1'}}"],
            ['ASSISTANT', 'Here is the plan'],
          ]),
          textEvent('USER', 'yes', '2025-01-01T07:01:00Z'),
          textEvent('ASSISTANT', 'Booked.', '2025-01-01T07:01:10Z'),
          textEvent('USER', 'yes', '2025-01-01T07:02:00Z'),
          textEvent('ASSISTANT', 'Confirmed the second flight.', '2025-01-01T07:02:10Z'),
        ],
      },
    ]);
    expect(rewrittenTurn).toEqual([
      'AgencyAgent/user: Plan a campaign',
      'AgencyAgent/assistant: Here is the plan',
      'AgencyAgent/user: yes',
      'AgencyAgent/assistant: Booked.',
      'AgencyAgent/user: yes',
      'AgencyAgent/assistant: Confirmed the second flight.',
    ]);
  });

  it('drops stringified tool use and tool result text', () => {
    expect(
      summarize([
        {
          agentName: 'AgencyAgent',
          events: [
            textEvent('USER', 'How many placements?', '2025-01-01T06:00:00Z'),
            textEvent('ASSISTANT', '{"toolUse": {"toolUseId": "tooluse_9", "name": "count"}}', '2025-01-01T06:00:01Z'),
            textEvent('USER', "[{'toolResult': {'tool_use_id': 'abc', 'content': '42'}}]", '2025-01-01T06:00:02Z'),
            {
              eventTimestamp: new Date('2025-01-01T06:00:02Z'),
              payload: [{ conversational: { role: 'TOOL', content: { text: '42' } } }],
            },
            textEvent('ASSISTANT', 'There are 42 placements.', '2025-01-01T06:00:03Z'),
          ],
        },
      ])
    ).toEqual(['AgencyAgent/user: How many placements?', 'AgencyAgent/assistant: There are 42 placements.']);
  });

  it('keeps visualization-data blocks in agent text', () => {
    const reply =
      'Campaign results:\n<visualization-data type="metrics-visualization">{"metrics":[{"name":"CTR","value":"1.2%"}]}</visualization-data>';

    const [entry] = eventsToHistory([
      { agentName: 'AgencyAgent', events: [textEvent('ASSISTANT', reply, '2025-01-01T05:00:00Z')] },
    ]);

    expect(entry.text).toBe(reply);
  });

  it('returns an empty history for no events', () => {
    expect(eventsToHistory([])).toEqual([]);
    expect(eventsToHistory([{ agentName: 'AgencyAgent', events: [] }])).toEqual([]);
    expect(
      eventsToHistory([
        {
          agentName: 'AgencyAgent',
          events: [
            { eventTimestamp: new Date('2025-01-01T04:00:00Z'), payload: [] },
            textEvent('ASSISTANT', '   ', '2025-01-01T04:00:01Z'),
          ],
        },
      ])
    ).toEqual([]);
  });
});
