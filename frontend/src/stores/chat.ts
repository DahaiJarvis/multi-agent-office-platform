import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: number
  agentName?: string
  intent?: string
  collaborationMode?: string
  feedback?: 'thumbs_up' | 'thumbs_down' | null
  streaming?: boolean
}

export const useChatStore = defineStore('chat', () => {
  const sessionId = ref('')
  const messages = ref<Message[]>([])
  const isStreaming = ref(false)
  const currentAgent = ref('')

  function addUserMessage(content: string) {
    messages.value.push({
      id: `msg-${Date.now()}`,
      role: 'user',
      content,
      timestamp: Date.now(),
    })
  }

  function addAssistantMessage(content: string, meta?: { agentName?: string; intent?: string; mode?: string }) {
    messages.value.push({
      id: `msg-${Date.now()}`,
      role: 'assistant',
      content,
      timestamp: Date.now(),
      agentName: meta?.agentName,
      intent: meta?.intent,
      collaborationMode: meta?.mode,
      feedback: null,
    })
  }

  function addStreamingMessage(): string {
    const id = `msg-${Date.now()}`
    messages.value.push({
      id,
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      streaming: true,
      feedback: null,
    })
    return id
  }

  function appendToStreamingMessage(id: string, chunk: string) {
    const msg = messages.value.find((m) => m.id === id)
    if (msg) {
      msg.content += chunk
    }
  }

  function finalizeStreamingMessage(id: string, meta?: { agentName?: string; intent?: string; mode?: string }) {
    const msg = messages.value.find((m) => m.id === id)
    if (msg) {
      msg.streaming = false
      msg.agentName = meta?.agentName
      msg.intent = meta?.intent
      msg.collaborationMode = meta?.mode
    }
  }

  function setMessageFeedback(messageId: string, feedback: 'thumbs_up' | 'thumbs_down' | null) {
    const msg = messages.value.find((m) => m.id === messageId)
    if (msg) {
      msg.feedback = feedback
    }
  }

  function setSessionId(id: string) {
    sessionId.value = id
  }

  function clearChat() {
    sessionId.value = ''
    messages.value = []
    currentAgent.value = ''
  }

  function loadHistory(historyMessages: any[]) {
    messages.value = historyMessages.map((m, i) => ({
      id: `msg-hist-${i}`,
      role: m.role,
      content: m.content,
      timestamp: new Date(m.timestamp || m.created_at || Date.now()).getTime(),
      agentName: m.metadata?.agent,
      intent: m.metadata?.intent,
      collaborationMode: m.metadata?.collaboration_mode,
      feedback: null,
    }))
  }

  return {
    sessionId,
    messages,
    isStreaming,
    currentAgent,
    addUserMessage,
    addAssistantMessage,
    addStreamingMessage,
    appendToStreamingMessage,
    finalizeStreamingMessage,
    setMessageFeedback,
    setSessionId,
    clearChat,
    loadHistory,
  }
})
