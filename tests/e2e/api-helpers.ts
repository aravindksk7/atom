import { APIRequestContext, request as pwRequest } from '@playwright/test';
import { BASE_URL } from '../../playwright.config';

export async function bootstrapAdminToken(): Promise<string> {
  const ctx = await pwRequest.newContext({ baseURL: BASE_URL });
  try {
    const resp = await ctx.post('/api/tokens', {
      data: { name: 'e2e-admin', is_admin: true },
    });
    if (!resp.ok()) {
      throw new Error(`bootstrap token creation failed: ${resp.status()} ${await resp.text()}`);
    }
    const body = await resp.json();
    return body.raw_token as string;
  } finally {
    await ctx.dispose();
  }
}

export function authedContext(token: string): Promise<APIRequestContext> {
  return pwRequest.newContext({
    baseURL: BASE_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${token}` },
  });
}
