/**
 * 企业级多智能体办公平台 - 嵌入式 Widget SDK
 *
 * 第三方应用通过此 SDK 将 AI 助手嵌入到自己的页面中。
 *
 * 使用方式：
 * ```html
 * <script src="https://your-domain.com/embed/sdk.js"></script>
 * <script>
 *   AgentWidget.init({
 *     token: 'embed.xxx.yyy',
 *     theme: 'light',
 *     position: 'bottom-right',
 *   });
 * </script>
 * ```
 */

;(function (global) {
  'use strict'

  var WIDGET_ID = 'agent-ai-widget'
  var DEFAULT_API_BASE = ''

  var defaultConfig = {
    token: '',
    theme: 'light',
    position: 'bottom-right',
    agentName: '',
    locale: 'zh-CN',
    apiBase: DEFAULT_API_BASE,
    width: 400,
    height: 600,
    primaryColor: '#4f46e5',
    features: {
      chat: true,
      streaming: true,
      fileUpload: false,
      voiceInput: false,
    },
  }

  var state = {
    initialized: false,
    open: false,
    config: null,
    accessToken: '',
    messages: [],
    loading: false,
  }

  function mergeConfig(userConfig) {
    var config = {}
    for (var key in defaultConfig) {
      config[key] = defaultConfig[key]
    }
    for (var key in userConfig) {
      if (userConfig[key] !== undefined) {
        config[key] = userConfig[key]
      }
    }
    return config
  }

  function createStyles(config) {
    var pos = config.position === 'bottom-left' ? 'left: 20px;' : 'right: 20px;'
    var themeBg = config.theme === 'dark' ? '#1e1e2e' : '#ffffff'
    var themeText = config.theme === 'dark' ? '#e0e0e0' : '#1a1a1a'
    var themeBorder = config.theme === 'dark' ? '#333' : '#e5e7eb'
    var themeInputBg = config.theme === 'dark' ? '#2a2a3e' : '#f9fafb'

    return '\
      #' + WIDGET_ID + '-container {\
        position: fixed;\
        bottom: 20px;\
        ' + pos + '\
        z-index: 999999;\
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;\
      }\
      #' + WIDGET_ID + '-toggle {\
        width: 56px;\
        height: 56px;\
        border-radius: 50%;\
        background: ' + config.primaryColor + ';\
        border: none;\
        cursor: pointer;\
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);\
        display: flex;\
        align-items: center;\
        justify-content: center;\
        transition: transform 0.2s, box-shadow 0.2s;\
        margin-left: auto;\
      }\
      #' + WIDGET_ID + '-toggle:hover {\
        transform: scale(1.05);\
        box-shadow: 0 6px 16px rgba(0,0,0,0.2);\
      }\
      #' + WIDGET_ID + '-toggle svg {\
        width: 28px;\
        height: 28px;\
        fill: white;\
      }\
      #' + WIDGET_ID + '-panel {\
        position: absolute;\
        bottom: 70px;\
        ' + (config.position === 'bottom-left' ? 'left: 0;' : 'right: 0;') + '\
        width: ' + config.width + 'px;\
        height: ' + config.height + 'px;\
        background: ' + themeBg + ';\
        border: 1px solid ' + themeBorder + ';\
        border-radius: 16px;\
        box-shadow: 0 8px 32px rgba(0,0,0,0.12);\
        display: none;\
        flex-direction: column;\
        overflow: hidden;\
      }\
      #' + WIDGET_ID + '-panel.open {\
        display: flex;\
      }\
      #' + WIDGET_ID + '-header {\
        padding: 16px;\
        background: ' + config.primaryColor + ';\
        color: white;\
        display: flex;\
        align-items: center;\
        justify-content: space-between;\
      }\
      #' + WIDGET_ID + '-header h3 {\
        margin: 0;\
        font-size: 16px;\
        font-weight: 600;\
      }\
      #' + WIDGET_ID + '-close {\
        background: none;\
        border: none;\
        color: white;\
        cursor: pointer;\
        font-size: 20px;\
        padding: 4px 8px;\
        border-radius: 4px;\
      }\
      #' + WIDGET_ID + '-close:hover {\
        background: rgba(255,255,255,0.2);\
      }\
      #' + WIDGET_ID + '-messages {\
        flex: 1;\
        overflow-y: auto;\
        padding: 16px;\
        color: ' + themeText + ';\
      }\
      .agent-msg {\
        margin-bottom: 12px;\
        padding: 10px 14px;\
        border-radius: 12px;\
        max-width: 85%;\
        word-wrap: break-word;\
        font-size: 14px;\
        line-height: 1.5;\
      }\
      .agent-msg.user {\
        background: ' + config.primaryColor + ';\
        color: white;\
        margin-left: auto;\
        border-bottom-right-radius: 4px;\
      }\
      .agent-msg.assistant {\
        background: ' + themeInputBg + ';\
        border-bottom-left-radius: 4px;\
      }\
      #' + WIDGET_ID + '-input-area {\
        padding: 12px;\
        border-top: 1px solid ' + themeBorder + ';\
        display: flex;\
        gap: 8px;\
      }\
      #' + WIDGET_ID + '-input {\
        flex: 1;\
        padding: 10px 14px;\
        border: 1px solid ' + themeBorder + ';\
        border-radius: 8px;\
        font-size: 14px;\
        background: ' + themeInputBg + ';\
        color: ' + themeText + ';\
        outline: none;\
      }\
      #' + WIDGET_ID + '-input:focus {\
        border-color: ' + config.primaryColor + ';\
      }\
      #' + WIDGET_ID + '-send {\
        padding: 10px 16px;\
        background: ' + config.primaryColor + ';\
        color: white;\
        border: none;\
        border-radius: 8px;\
        cursor: pointer;\
        font-size: 14px;\
        font-weight: 500;\
      }\
      #' + WIDGET_ID + '-send:hover {\
        opacity: 0.9;\
      }\
      #' + WIDGET_ID + '-send:disabled {\
        opacity: 0.5;\
        cursor: not-allowed;\
      }'
  }

  function injectStyles(css) {
    var style = document.createElement('style')
    style.type = 'text/css'
    style.textContent = css
    document.head.appendChild(style)
  }

  function createWidgetDOM(config) {
    var container = document.createElement('div')
    container.id = WIDGET_ID + '-container'

    var panel = document.createElement('div')
    panel.id = WIDGET_ID + '-panel'

    var header = document.createElement('div')
    header.id = WIDGET_ID + '-header'
    header.innerHTML = '<h3>' + (config.agentName || 'AI 助手') + '</h3>' +
      '<button id="' + WIDGET_ID + '-close" aria-label="关闭">&times;</button>'

    var messages = document.createElement('div')
    messages.id = WIDGET_ID + '-messages'

    var inputArea = document.createElement('div')
    inputArea.id = WIDGET_ID + '-input-area'
    inputArea.innerHTML = '<input id="' + WIDGET_ID + '-input" type="text" placeholder="输入消息..." />' +
      '<button id="' + WIDGET_ID + '-send">发送</button>'

    panel.appendChild(header)
    panel.appendChild(messages)
    panel.appendChild(inputArea)

    var toggle = document.createElement('button')
    toggle.id = WIDGET_ID + '-toggle'
    toggle.setAttribute('aria-label', '打开 AI 助手')
    toggle.innerHTML = '<svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.17L4 17.17V4h16v12z"/></svg>'

    container.appendChild(panel)
    container.appendChild(toggle)

    document.body.appendChild(container)

    toggle.addEventListener('click', toggleWidget)
    document.getElementById(WIDGET_ID + '-close').addEventListener('click', toggleWidget)
    document.getElementById(WIDGET_ID + '-send').addEventListener('click', sendMessage)
    document.getElementById(WIDGET_ID + '-input').addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        sendMessage()
      }
    })
  }

  function toggleWidget() {
    state.open = !state.open
    var panel = document.getElementById(WIDGET_ID + '-panel')
    if (state.open) {
      panel.classList.add('open')
      document.getElementById(WIDGET_ID + '-input').focus()
    } else {
      panel.classList.remove('open')
    }
  }

  function appendMessage(role, content) {
    var messagesEl = document.getElementById(WIDGET_ID + '-messages')
    var msgEl = document.createElement('div')
    msgEl.className = 'agent-msg ' + role
    msgEl.textContent = content
    messagesEl.appendChild(msgEl)
    messagesEl.scrollTop = messagesEl.scrollHeight
  }

  function sendMessage() {
    var input = document.getElementById(WIDGET_ID + '-input')
    var text = input.value.trim()
    if (!text || state.loading) return

    input.value = ''
    appendMessage('user', text)
    state.loading = true
    document.getElementById(WIDGET_ID + '-send').disabled = true

    if (state.config.features.streaming) {
      sendStreaming(text)
    } else {
      sendSync(text)
    }
  }

  function sendSync(text) {
    var apiBase = state.config.apiBase || ''
    fetch(apiBase + '/api/v1/agent/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + state.accessToken,
        'X-Embed-Token': state.config.token,
      },
      body: JSON.stringify({
        message: text,
        session_id: state.sessionId || '',
      }),
    })
      .then(function (res) { return res.json() })
      .then(function (data) {
        var content = data.data?.response || data.response || '抱歉，未能获取响应'
        appendMessage('assistant', content)
        if (data.data?.session_id) state.sessionId = data.data.session_id
      })
      .catch(function (err) {
        appendMessage('assistant', '请求失败，请稍后重试')
        console.error('[AgentWidget] 请求失败:', err)
      })
      .finally(function () {
        state.loading = false
        document.getElementById(WIDGET_ID + '-send').disabled = false
      })
  }

  function sendStreaming(text) {
    var apiBase = state.config.apiBase || ''
    var assistantEl = document.createElement('div')
    assistantEl.className = 'agent-msg assistant'
    assistantEl.textContent = ''
    document.getElementById(WIDGET_ID + '-messages').appendChild(assistantEl)

    fetch(apiBase + '/api/v1/agent/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + state.accessToken,
        'X-Embed-Token': state.config.token,
      },
      body: JSON.stringify({
        message: text,
        session_id: state.sessionId || '',
      }),
    })
      .then(function (response) {
        var reader = response.body.getReader()
        var decoder = new TextDecoder()
        var buffer = ''

        function read() {
          reader.read().then(function (result) {
            if (result.done) {
              state.loading = false
              document.getElementById(WIDGET_ID + '-send').disabled = false
              return
            }

            buffer += decoder.decode(result.value, { stream: true })
            var lines = buffer.split('\n')
            buffer = lines.pop()

            for (var i = 0; i < lines.length; i++) {
              var line = lines[i].trim()
              if (line.startsWith('data: ')) {
                var data = line.slice(6)
                if (data === '[DONE]') continue
                try {
                  var parsed = JSON.parse(data)
                  if (parsed.token) {
                    assistantEl.textContent += parsed.token
                    document.getElementById(WIDGET_ID + '-messages').scrollTop =
                      document.getElementById(WIDGET_ID + '-messages').scrollHeight
                  }
                  if (parsed.session_id) state.sessionId = parsed.session_id
                } catch (e) {}
              }
            }

            read()
          })
        }

        read()
      })
      .catch(function (err) {
        assistantEl.textContent = '请求失败，请稍后重试'
        state.loading = false
        document.getElementById(WIDGET_ID + '-send').disabled = false
        console.error('[AgentWidget] 流式请求失败:', err)
      })
  }

  var AgentWidget = {
    init: function (userConfig) {
      if (state.initialized) {
        console.warn('[AgentWidget] 已初始化，请勿重复调用')
        return
      }

      if (!userConfig || !userConfig.token) {
        console.error('[AgentWidget] 必须提供 embed token')
        return
      }

      state.config = mergeConfig(userConfig)
      state.sessionId = 'embed-' + Date.now() + '-' + Math.random().toString(36).substr(2, 8)

      injectStyles(createStyles(state.config))
      createWidgetDOM(state.config)
      state.initialized = true

      console.log('[AgentWidget] 初始化完成')
    },

    destroy: function () {
      var container = document.getElementById(WIDGET_ID + '-container')
      if (container) container.remove()
      state.initialized = false
      state.open = false
      state.messages = []
    },

    open: function () {
      if (!state.initialized) return
      if (!state.open) toggleWidget()
    },

    close: function () {
      if (!state.initialized) return
      if (state.open) toggleWidget()
    },

    on: function (event, callback) {
      document.addEventListener('agent-widget:' + event, function (e) {
        callback(e.detail)
      })
    },
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = AgentWidget
  } else {
    global.AgentWidget = AgentWidget
  }
})(typeof window !== 'undefined' ? window : this)
