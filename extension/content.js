;(() => {
	const bridgeUrl = 'http://127.0.0.1:8765/draft'
	const logUrl = 'http://127.0.0.1:8765/log'
	const recommendationsUrl = 'http://127.0.0.1:8765/recommendations'
	let busy = false
	let lastPayloadSignature = ''
	let lastRecommendationsSignature = ''
	let lastRecommendationsSentAt = 0
	let lastNoRecommendationsNoticeAt = 0
	let lastRecommendationsReadAt = 0
	let recommendationsSyncing = false
	let recommendationsObserver
	let observedRecommendationsSection
	let recommendationSyncTimer
	let waitingForReset = false
	let resetGeneration = null
	let panel
	let recommendationsPanel
	const recommendationsBySide = { Radiant: [], Dire: [] }
	const DEBUG = false

	const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
	async function waitFor(predicate, timeout = 2500, interval = 50) {
		const deadline = Date.now() + timeout
		while (Date.now() < deadline) {
			const value = predicate()
			if (value) return value
			await sleep(interval)
		}
		return null
	}

	const normalize = value =>
		String(value || '')
			.trim()
			.toLowerCase()
			.replace(/[^a-z0-9]/g, '')
	function debug(level, message, details = {}) {
		if (!DEBUG && level !== 'ERROR') return
		console[level === 'ERROR' ? 'error' : level === 'WARN' ? 'warn' : 'log'](
			`[D2PT Bridge ${level}] ${message}`,
			details,
		)
	}

	function bridgeLog(event, details = {}) {
		fetch(logUrl, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ event, details }),
			cache: 'no-store',
			keepalive: true,
		}).catch(() => {})
	}

	function showStatus(message) {
		if (!panel) {
			panel = document.createElement('div')
			panel.id = 'd2pt-draft-bridge-status'
			Object.assign(panel.style, {
				position: 'fixed',
				right: '14px',
				bottom: '14px',
				zIndex: '2147483647',
				width: '390px',
				maxWidth: '90vw',
				padding: '10px 12px',
				borderRadius: '8px',
				background: '#171717',
				color: '#fff',
				font: '12px/1.4 system-ui',
				boxShadow: '0 4px 18px #0008',
				whiteSpace: 'pre-wrap',
			})
			document.documentElement.appendChild(panel)
		}

		let label = panel.querySelector('#d2pt-bridge-status-label')
		if (!label) {
			label = document.createElement('div')
			label.id = 'd2pt-bridge-status-label'
			panel.prepend(label)
		}

		label.textContent = `D2PT Bridge: ${message}`
	}

	function showRecommendations() {
		if (!recommendationsPanel) {
			recommendationsPanel = document.createElement('div')
			recommendationsPanel.id = 'd2pt-bridge-recommendations'
			Object.assign(recommendationsPanel.style, {
				position: 'fixed',
				left: '14px',
				bottom: '14px',
				zIndex: '2147483647',
				width: '220px',
				maxWidth: 'calc(100vw - 28px)',
				padding: '10px 12px',
				borderRadius: '8px',
				background: '#171717',
				color: '#fff',
				font: '12px/1.4 system-ui',
				boxShadow: '0 4px 18px #0008',
			})
			document.documentElement.appendChild(recommendationsPanel)
		}

		recommendationsPanel.replaceChildren()
		const title = document.createElement('div')
		title.textContent = 'D2PT recommendations'
		title.style.fontWeight = '700'
		title.style.marginBottom = '6px'
		recommendationsPanel.appendChild(title)

		for (const side of ['Radiant', 'Dire']) {
			const heading = document.createElement('div')
			heading.textContent = side
			heading.style.cssText = 'margin-top: 6px; color: #8be9fd; font-weight: 700'
			recommendationsPanel.appendChild(heading)

			for (const item of recommendationsBySide[side]) {
				const button = document.createElement('button')
				button.type = 'button'
				button.title = `Copy ${item.name}`
				button.textContent = item.name
				Object.assign(button.style, {
					display: 'block',
					width: '100%',
					padding: '3px 0',
					border: '0',
					background: 'transparent',
					color: '#d8f7ff',
					cursor: 'pointer',
					font: 'inherit',
					textAlign: 'left',
				})
				button.addEventListener('click', async () => {
					try {
						await navigator.clipboard.writeText(item.name)
						showStatus(`Copied ${item.name} from recommendations.`)
					} catch (error) {
						bridgeLog('recommendation-copy-error', { message: String(error) })
					}
				})
				recommendationsPanel.appendChild(button)
			}
		}
	}

	function visible(element) {
		if (!element || !element.getClientRects().length) return false
		const rect = element.getBoundingClientRect()
		return rect.width > 0 && rect.height > 0
	}

	function getSlots(side) {
		const lowerSide = side.toLowerCase()
		return [...document.querySelectorAll('button[aria-label]')]
			.filter(button => {
				const label = (button.getAttribute('aria-label') || '').toLowerCase()
				return (
					(label.startsWith(`pick hero for ${lowerSide} slot `) ||
						label.startsWith(`change ${lowerSide} slot `)) &&
					visible(button)
				)
			})
			.sort((a, b) => {
				const getSlotIndex = button =>
					Number((button.getAttribute('aria-label') || '').match(/slot (\d+)/i)?.[1] || 99)
				return getSlotIndex(a) - getSlotIndex(b)
			})
	}

	function findTargetSlot(side, slotIndex) {
		const slots = getSlots(side)
		const requested = slots.find(button =>
			Number((button.getAttribute('aria-label') || '').match(/slot (\d+)/i)?.[1]) === slotIndex,
		)
		// Prefer the matching Dota slot.  If it is empty, this fills it; if it
		// already contains an obsolete hero, opening it replaces that hero.
		return requested || slots.find(button => /^pick$/i.test(button.innerText.trim())) || null
	}

	function hasHeroOnSide(side, heroName) {
		const target = normalize(heroName)
		const lowerSide = side.toLowerCase()
		return [...document.querySelectorAll('button[aria-label]')].some(button => {
			const label = (button.getAttribute('aria-label') || '').toLowerCase()
			if (!label.startsWith(`change ${lowerSide} slot `)) return false
			return [...button.querySelectorAll('img[alt]')].some(image => normalize(image.alt) === target)
		})
	}

	function selectedHeroCount(side) {
		const lowerSide = side.toLowerCase()
		return [...document.querySelectorAll('button[aria-label]')].filter(button =>
			(button.getAttribute('aria-label') || '').toLowerCase().startsWith(`change ${lowerSide} slot `),
		).length
	}

	function setInputValue(input, value) {
		const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
		if (setter) setter.call(input, value)
		else input.value = value

		input.dispatchEvent(new Event('input', { bubbles: true }))
		input.dispatchEvent(new Event('change', { bubbles: true }))
	}

	function closeHeroPicker() {
		const pickerSearch = [...document.querySelectorAll('input')].find(input => {
			if (!visible(input)) return false
			const label = `${input.placeholder} ${input.getAttribute('aria-label') || ''}`.toLowerCase()
			return /search\s+heroes/.test(label) && !/players|account|teams|tournaments/.test(label)
		})
		if (!pickerSearch) return

		document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true }))
	}

	async function findHeroButton(heroName) {
		const target = normalize(heroName)
		const findSearch = () =>
			[...document.querySelectorAll('input')].find(input => {
				if (!visible(input)) return false
				const label = `${input.placeholder} ${input.getAttribute('aria-label') || ''}`.toLowerCase()
				return /search\s+heroes/.test(label) && !/players|account|teams|tournaments/.test(label)
			})
		const search = await waitFor(findSearch)

		if (!search) {
			bridgeLog('hero-search', { heroName, searchFound: false })
			debug('ERROR', 'Slot hero search not found', { heroName })
			return null
		}

		bridgeLog('hero-search', { heroName, searchFound: true })
		setInputValue(search, heroName)
		const findMatch = () => {
			const picker = findSearch()?.closest(
				'[role="dialog"], [class*="picker" i], [class*="overlay" i], [class*="modal" i], [class*="fixed" i], .fixed, [style*="fixed"]',
			)
			if (!picker) return null

			return [...picker.querySelectorAll('button')].find(button => {
				if (!visible(button)) return false
				const image = button.querySelector('img[alt]')
				const lines = button.innerText
					.split(/\r?\n/)
					.map(line => normalize(line))
					.filter(Boolean)
				return normalize(image?.alt) === target && lines.includes(target)
			})
		}
		const heroButton = await waitFor(findMatch)
		bridgeLog('hero-card', { heroName, found: Boolean(heroButton) })

		debug(heroButton ? 'INFO' : 'ERROR', 'Hero card search result', {
			heroName,
		})
		return heroButton
	}

	async function pickHero(side, heroName, slotIndex) {
		if (hasHeroOnSide(side, heroName)) {
			debug('INFO', 'Hero already placed on requested side', { side, heroName })
			return 'already-picked'
		}

		const slot = findTargetSlot(side, slotIndex)
		if (!slot) {
			debug('ERROR', 'No editable slot on requested side', { side, heroName, slotIndex })
			return 'no-empty-slot'
		}

		const slotLabel = slot.getAttribute('aria-label')
		const slotNumber = Number(slotLabel?.match(/slot (\d+)/i)?.[1])
		bridgeLog('slot-open', { side, heroName, slotLabel })
		debug('INFO', 'Opening target slot', { side, heroName, slotLabel })
		slot.click()
		const isTargetActive = () => {
			const activeLabel = document.querySelector('button[aria-current="true"][aria-label]')?.getAttribute('aria-label') || ''
			return new RegExp(`(?:pick hero for|change) ${side} slot ${slotNumber}$`, 'i').test(activeLabel)
		}
		const activeSlot = await waitFor(
			isTargetActive,
		)
		if (!activeSlot) {
			bridgeLog('slot-open-result', { side, heroName, slotLabel, active: false })
			return 'slot-not-opened'
		}
		bridgeLog('slot-open-result', { side, heroName, slotLabel, active: true })

		const heroButton = await findHeroButton(heroName)
		if (!heroButton) return 'not-found'

		if (!isTargetActive()) {
			return 'slot-changed'
		}

		const selectedBefore = selectedHeroCount(side)
		bridgeLog('hero-click', { side, heroName, slotLabel, selectedBefore })
		heroButton.click()
		return await waitFor(() => {
			if (hasHeroOnSide(side, heroName)) return 'picked'
			return selectedHeroCount(side) > selectedBefore ? 'wrong-hero' : null
		}) || 'not-confirmed'
	}

	async function pickHeroWithRetries(side, heroName, slotIndex) {
		let lastResult = 'not-confirmed'
		for (let attempt = 1; attempt <= 3; attempt += 1) {
			const result = await pickHero(side, heroName, slotIndex)
			bridgeLog('pick-attempt', { side, heroName, slotIndex, attempt, result })
			if (['picked', 'already-picked', 'no-empty-slot', 'wrong-hero'].includes(result)) return result
			lastResult = result
			debug('WARN', 'Pick attempt failed', { side, heroName, attempt, result })
			await sleep(250)
		}
		return lastResult
	}

	async function applySide(side, heroes) {
		const results = []

		for (const [index, rawName] of heroes.slice(0, 5).entries()) {
			const heroName = String(rawName || '').trim()
			if (!heroName) continue

			const result = await pickHeroWithRetries(side, heroName, index + 1)
			results.push(`${side} ${heroName} — ${result}`)

			if (!['picked', 'already-picked'].includes(result)) break
		}

		return results
	}

	function getRecommendationsSection() {
		const heading = [...document.querySelectorAll('span')].find(
			element => normalize(element.textContent) === 'recommendedpicks',
		)
		return heading?.closest('div.rounded-2xl') || heading?.parentElement?.parentElement || null
	}

	function recommendationSignature(result) {
		return JSON.stringify(result?.items || [])
	}

	function scheduleRecommendationsSync() {
		clearTimeout(recommendationSyncTimer)
		recommendationSyncTimer = setTimeout(() => {
			if (!busy) syncRecommendations()
		}, 250)
	}

	function observeRecommendationsSection() {
		const section = getRecommendationsSection()
		if (!section || section === observedRecommendationsSection) return
		recommendationsObserver?.disconnect()
		observedRecommendationsSection = section
		recommendationsObserver = new MutationObserver(scheduleRecommendationsSync)
		recommendationsObserver.observe(section, {
			childList: true,
			subtree: true,
			attributes: true,
			characterData: true,
		})
	}

	function readRecommendations() {
		const section = getRecommendationsSection()
		if (!section) return null

		const sideButton = [...section.querySelectorAll('button')].find(
			b => ['radiant', 'dire'].includes(normalize(b.innerText)) && b.getAttribute('aria-pressed') === 'true',
		)
		if (!sideButton) return null

		const collectItems = root =>
			[...root.querySelectorAll('button')]
				.map(button => {
					const image = button.querySelector('img[alt]')
					const name = image?.alt.trim() || ''
					const text = button.innerText || ''
					if (!name || !/\d[\d,]*\s+matches/i.test(text)) return null

					const roleImage = button.querySelector('img[src*="/pos/"]')
					return {
						name,
						role: roleImage?.parentElement?.innerText.trim() || '',
						matches: text.match(/[\d,]+\s+matches/i)?.[0] || '',
					}
				})
				.filter(Boolean)

		let items = collectItems(section)
		if (!items.length) items = collectItems(document)

		return { side: sideButton.innerText.trim(), items: items.slice(0, 5) }
	}

	async function readAllRecommendations() {
		const current = readRecommendations()
		if (!current) return null

		const section = getRecommendationsSection()
		const originalSide = current.side
		const results = {}
		for (const side of ['Radiant', 'Dire']) {
			const button = [...section.querySelectorAll('button')].find(
				candidate => normalize(candidate.innerText) === normalize(side),
			)
			if (!button) continue
			let displayed = readRecommendations()
			const beforeSignature = recommendationSignature(displayed)
			if (displayed?.side !== side) button.click()

			const switched = await waitFor(() => {
				const updated = readRecommendations()
				return updated?.side === side ? updated : null
			}, 500)
			if (!switched) continue

			// Changing the active tab happens before React replaces its cards.  Do
			// not accept the old tab's cards merely because the button changed.
			const result = await waitFor(() => {
				const updated = readRecommendations()
				if (updated?.side !== side || !updated.items.length) return null
				return recommendationSignature(updated) !== beforeSignature ? updated : null
			}, 1200)
			const confirmed = result || (displayed?.side === side ? switched : null)
			if (confirmed) {
				results[side] = confirmed.items
				recommendationsBySide[side] = confirmed.items
			}
		}

		const restoreButton = [...section.querySelectorAll('button')].find(
			button => normalize(button.innerText) === normalize(originalSide),
		)
		if (restoreButton && originalSide !== readRecommendations()?.side) restoreButton.click()
		return results
	}

	async function syncRecommendations() {
		if (recommendationsSyncing || Date.now() - lastRecommendationsReadAt < 800) return
		recommendationsSyncing = true
		lastRecommendationsReadAt = Date.now()
		try {
			observeRecommendationsSection()
			const result = await readAllRecommendations()
			if (!result) return
			const snapshot = { radiant: result.Radiant || [], dire: result.Dire || [] }
			recommendationsBySide.Radiant = snapshot.radiant
			recommendationsBySide.Dire = snapshot.dire
			if (!snapshot.radiant.length && !snapshot.dire.length) {
				if (Date.now() - lastNoRecommendationsNoticeAt > 8000) {
					debug('WARN', 'No confirmed recommendation cards found')
					lastNoRecommendationsNoticeAt = Date.now()
				}
				return
			}
			lastNoRecommendationsNoticeAt = 0
			showRecommendations()

			const signature = JSON.stringify(snapshot)
			if (signature === lastRecommendationsSignature && Date.now() - lastRecommendationsSentAt < 800) return

			const response = await fetch(recommendationsUrl, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: signature,
				cache: 'no-store',
			})
			if (!response.ok) return
			lastRecommendationsSignature = signature
			lastRecommendationsSentAt = Date.now()
		} catch (error) {
			debug('ERROR', 'Recommendation sync failed', { message: String(error) })
			bridgeLog('recommendations-error', { message: String(error) })
		} finally {
			recommendationsSyncing = false
		}
	}

	async function resetBridgeFromSite(event) {
		const button = event.target instanceof Element ? event.target.closest('button') : null
		if (!button || (normalize(button.innerText) !== 'reset' && normalize(button.getAttribute('aria-label')) !== 'reset')) return

		waitingForReset = true
		resetGeneration = null
		lastPayloadSignature = ''
		showStatus('Reset detected. Clearing the bridge draft…')

		try {
			const response = await fetch('http://127.0.0.1:8765/reset', {
				method: 'POST',
				cache: 'no-store',
			})
			if (!response.ok) throw new Error(`Bridge reset returned HTTP ${response.status}`)
			const result = await response.json()
			resetGeneration = result.reset_generation
			showStatus('Reset received. Waiting for the next Dota draft…')
		} catch (error) {
			debug('ERROR', 'Bridge reset failed', { message: String(error) })
			bridgeLog('reset-error', { message: String(error) })
			showStatus('Could not reset the bridge. Make sure it is running, then press Reset again.')
		}
	}

	document.addEventListener('click', resetBridgeFromSite, true)

	async function sync() {
		if (busy || !location.pathname.includes('/drafter')) return
		busy = true

		try {
			const response = await fetch(bridgeUrl, { cache: 'no-store' })
			if (!response.ok) {
				showStatus(`Local bridge returned HTTP ${response.status}`)
				return
			}

			const draft = await response.json()
			await syncRecommendations()
			if (draft.watching === false) {
				lastPayloadSignature = ''
				showStatus('Auto-sync paused. Resume it in the recommendations panel.')
				return
			}
			const radiant = Array.isArray(draft.radiant) ? draft.radiant.slice(0, 5) : []
			const dire = Array.isArray(draft.dire) ? draft.dire.slice(0, 5) : []
			const signature = JSON.stringify([radiant, dire])

			if (waitingForReset) {
				if (!Number.isInteger(draft.reset_generation) || resetGeneration === null || draft.reset_generation < resetGeneration) return
				waitingForReset = false
				resetGeneration = null
				lastPayloadSignature = ''
			}

			if (!draft.updated || (!radiant.length && !dire.length)) {
				lastPayloadSignature = ''
				return
			}

			if (signature === lastPayloadSignature) return
			bridgeLog('draft-received', { radiant, dire })

			if (!document.querySelector('button[aria-label$="slot 1" i]')) {
				bridgeLog('draft-slots-not-found')
				showStatus('Draft slots not found. Open the Draft tab and refresh the page.')
				return
			}

			showStatus('Draft received. Applying picks…')
			debug('INFO', 'Draft received', { radiant, dire })

			const results = [...(await applySide('radiant', radiant)), ...(await applySide('dire', dire))]

			const isCompleted = result => result.endsWith('— picked') || result.endsWith('— already-picked')
			const failures = results.filter(result => !isCompleted(result))
			const picked = results.filter(result => result.endsWith('— picked')).length
			const skipped = results.filter(result => result.endsWith('— already-picked')).length

			if (failures.length) {
				bridgeLog('draft-application-failed', { results })
				showStatus(`Applied ${picked} pick(s), then stopped to avoid incorrect picks.\n${failures.join('\n')}`)
				debug('ERROR', 'Draft application stopped', { results })
				return
			}

			lastPayloadSignature = signature
			closeHeroPicker()
			bridgeLog('draft-application-complete', { results })
			showStatus(`Applied ${picked} pick(s); skipped ${skipped} already placed.`)
			debug('INFO', 'Draft application complete', { results })
		} catch (error) {
			debug('ERROR', 'Sync failed', { message: String(error), stack: error?.stack })
			bridgeLog('sync-error', { message: String(error) })
			showStatus('Bridge connection failed. Make sure it is running at 127.0.0.1:8765.')
		} finally {
			busy = false
		}
	}

	setInterval(sync, 1000)
	showStatus('Extension loaded. Waiting for local draft feed…')
})()
