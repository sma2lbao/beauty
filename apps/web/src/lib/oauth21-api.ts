/**
 * OAuth 2.1 授权页数据层:经 BFF 调 account-service。
 */
import { requestJson } from '@/lib/http';

export type OAuthClientInfo = {
  client_id: string;
  client_name: string;
  redirect_uris: string[];
  scopes: string[];
};

export type OAuthApproveInput = {
  client_id: string;
  redirect_uri: string;
  scope: string;
  state: string | null;
  code_challenge: string;
  code_challenge_method: 'S256';
};

const PATH = '/api/account/v1/oauth';

export const oauth21Api = {
  client: (clientId: string) =>
    requestJson<OAuthClientInfo>(
      '',
      PATH + '/clients/' + encodeURIComponent(clientId),
    ),
  approve: (input: OAuthApproveInput) =>
    requestJson<{ redirect_to: string }>('', PATH + '/authorize/approve', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
};
