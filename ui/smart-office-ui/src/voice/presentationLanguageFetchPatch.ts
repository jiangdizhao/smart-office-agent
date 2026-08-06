import { visitLanguagePreference } from './visitLanguagePreference'

const PATCH_KEY = '__smartOfficePresentationLanguageFetchPatchInstalled__'
const runtimeWindow = window as typeof window & Record<string, unknown>

if (!runtimeWindow[PATCH_KEY]) {
  runtimeWindow[PATCH_KEY] = true
  const previousFetch = window.fetch.bind(window)

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const source = input instanceof Request ? input.url : String(input)
    let url: URL | null = null
    try {
      url = new URL(source, window.location.href)
    } catch {
      url = null
    }

    if (url?.pathname.endsWith('/api/presentation/session/script')) {
      url.searchParams.set('language', visitLanguagePreference.current())
      if (input instanceof Request) {
        return await previousFetch(new Request(url.toString(), input), init)
      }
      return await previousFetch(url.toString(), init)
    }

    return await previousFetch(input, init)
  }
}
