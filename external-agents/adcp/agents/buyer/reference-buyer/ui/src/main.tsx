import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import { App } from './App';
import './index.css';
// The journey view's own stylesheet, scoped to `.journey-root` so it cannot reach the chat view.
import './styles/journey.css';
// Bind and Plan visuals. Separate from journey.css because that file is a byte-checked 1:1 port of the
// prototype and may not gain rules; see journeyGovernance.css's own header.
import './styles/journeyGovernance.css';
// The horizontal flow grid. Must come after journey.css: it overrides the ported `.flow` rule at equal
// specificity, so import order is what decides the winner.
import './styles/journeyFlow.css';
import './styles/journey-session.css';
import './styles/journey-products.css';
import './styles/journey-gravity.css';
// The Media Buy package strip. Ours, not the byte-checked journey.css; see its own header.
import './styles/journey-mediabuy.css';
// The Accounts per-seller strip. Ours, same reason; mirrors the media buy strip's grammar.
import './styles/journey-accounts.css';
// The plan focus control. Ours; lets the journey follow a plan across sessions.
import './styles/journey-plan.css';

const container = document.getElementById('root');
if (!container) {
  // A missing root is a broken build, not a runtime condition to paper over.
  throw new Error('#root not found in index.html');
}

// The router lives here rather than inside `App`, so tests can mount `App` under a `MemoryRouter` and
// choose which route they are exercising.
createRoot(container).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
