import http from './request'

export interface IntentDefinition {
  name: string
  label: string
  description: string
}

export interface IntentExample {
  input: string
  output: string
  reason: string
}

export interface IntentListResult {
  intents: IntentDefinition[]
  examples: IntentExample[]
  total: number
}

export interface IntentConfig {
  intent: string
  mode: string
  review: boolean
}

export interface CapabilityCard {
  agent_name: string
  description: string
  version: string
  category: string
  supported_intents: string[]
  intent_configs: IntentConfig[]
  required_services: string[]
  security_constraints: string[]
  priority: number
  enabled: boolean
}

export interface RoutingEntry {
  intent: string
  agent: string
  mode: string
  review: boolean
}

export interface RoutingTableResult {
  routes: RoutingEntry[]
  total: number
}

export const routingApi = {
  getIntents() {
    return http.get<IntentListResult>('/debug/intents')
  },

  getCapabilities() {
    return http.get<CapabilityCard[]>('/debug/capabilities')
  },

  getRouting() {
    return http.get<RoutingTableResult>('/debug/routing')
  },
}
