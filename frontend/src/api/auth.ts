import http from './request'

export interface LoginParams {
  user_id: string
  password: string
  channel?: string
}

export interface LoginResult {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user_id: string
  roles: string[]
}

export interface SSOAuthorizeParams {
  provider: string
  redirect_uri?: string
}

export interface SSOAuthorizeResult {
  authorization_url: string
  state: string
  provider: string
}

export interface SSOCallbackParams {
  provider: string
  code: string
  state: string
}

export interface SSOProvidersResult {
  providers: string[]
}

export const authApi = {
  login(params: LoginParams) {
    return http.post<LoginResult>('/auth/login', params)
  },

  logout(refreshToken?: string) {
    return http.post('/auth/logout', { refresh_token: refreshToken || '' })
  },

  refresh(refreshToken: string) {
    return http.post('/auth/refresh', { refresh_token: refreshToken })
  },

  getSSOProviders() {
    return http.get<SSOProvidersResult>('/auth/sso/providers')
  },

  ssoAuthorize(params: SSOAuthorizeParams) {
    return http.post<SSOAuthorizeResult>('/auth/sso/authorize', params)
  },

  ssoCallback(params: SSOCallbackParams) {
    return http.post<LoginResult>('/auth/sso/callback', params)
  },
}
