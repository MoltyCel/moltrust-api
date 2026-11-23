// Express: x402 payment middleware with the MolTrust hook before verify and settle.
//
//   npm install && node express.mjs
//
// Starts a server on a free local port, sends four requests, prints what each
// one got, and exits 0 only if every answer is the expected one.

import express from 'express';
import { paymentMiddleware } from '@x402/express';
import { ROUTES, resourceServer, runScenario } from './setup.mjs';

const log = (s) => console.log(s);
const app = express();
app.use(paymentMiddleware(ROUTES, resourceServer(log)));
app.get('/report', (req, res) => res.json({ report: 'paid content' }));

const server = app.listen(0, '127.0.0.1', async () => {
  const { port } = server.address();
  console.log(`express on 127.0.0.1:${port}`);
  let ok = false;
  try {
    ok = await runScenario(`http://127.0.0.1:${port}`, log);
  } finally {
    server.close();
  }
  console.log(ok ? 'express: all four answers as expected' : 'express: UNEXPECTED ANSWER');
  process.exitCode = ok ? 0 : 1;
});
