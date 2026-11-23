// Hono: x402 payment middleware with the MolTrust hook before verify and settle.
//
//   npm install && node hono.mjs
//
// Starts a server on a free local port, sends four requests, prints what each
// one got, and exits 0 only if every answer is the expected one.

import { Hono } from 'hono';
import { serve } from '@hono/node-server';
import { paymentMiddleware } from '@x402/hono';
import { ROUTES, resourceServer, runScenario } from './setup.mjs';

const log = (s) => console.log(s);
const app = new Hono();
app.use(paymentMiddleware(ROUTES, resourceServer(log)));
app.get('/report', (c) => c.json({ report: 'paid content' }));

const server = serve({ fetch: app.fetch, port: 0, hostname: '127.0.0.1' }, async ({ port }) => {
  console.log(`hono on 127.0.0.1:${port}`);
  let ok = false;
  try {
    ok = await runScenario(`http://127.0.0.1:${port}`, log);
  } finally {
    server.close();
  }
  console.log(ok ? 'hono: all four answers as expected' : 'hono: UNEXPECTED ANSWER');
  process.exitCode = ok ? 0 : 1;
});
