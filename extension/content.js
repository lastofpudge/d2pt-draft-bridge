;(() => {
	const bridgeUrl = 'http://127.0.0.1:8765/draft'
	const recommendationsUrl = 'http://127.0.0.1:8765/recommendations'
	let busy = false
	let lastPayloadSignature = ''
	let lastRecommendationsSignature = ''
	let lastRecommendationsSentAt = 0
	let lastNoRecommendationsNoticeAt = 0
	let waitingForReset = false
	let resetGeneration = null
	let panel
	const DEBUG = false
	const logs = []

	const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
	const normalize = value =>
		String(value || '')
			.trim()
			.toLowerCase()
			.replace(/[^a-z0-9]/g, '')
	function debug(level, message, details = {}) {
		if (!DEBUG) return

		const entry = { time: new Date().toLocaleTimeString(), level, message, details }
		logs.push(entry)
		if (logs.length > 100) logs.shift()

		if (DEBUG) {
			const output = JSON.stringify(details ?? {})
			console[level === 'ERROR' ? 'error' : level === 'WARN' ? 'warn' : 'log'](`[D2PT Bridge ${level}] ${message} ${output}`)
		}

		renderLog()
	}

	function renderLog() {
		if (!panel || !logs.length) return

		let box = panel.querySelector('#d2pt-bridge-log')
		if (!box) {
			box = document.createElement('pre')
			box.id = 'd2pt-bridge-log'
			Object.assign(box.style, {
				margin: '8px 0 0',
				maxHeight: '180px',
				overflowY: 'auto',
				font: '11px/1.4 monospace',
				color: '#ddd',
				whiteSpace: 'pre-wrap',
				overflowWrap: 'anywhere',
			})
			panel.appendChild(box)
		}

		box.textContent = logs
			.slice(-12)
			.map(entry => `${entry.time} [${entry.level}] ${entry.message} ${JSON.stringify(entry.details)}`)
			.join('\n')
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
		renderLog()
	}

	function visible(element) {
		if (!element || !element.getClientRects().length) return false
		const rect = element.getBoundingClientRect()
		return rect.width > 0 && rect.height > 0
	}

	function getSlots(side) {
		return [...document.querySelectorAll('button[aria-label]')]
			.filter(button => {
				const label = button.getAttribute('aria-label') || ''
				return label.startsWith(`Pick hero for ${side} slot `) && visible(button)
			})
			.sort((a, b) => {
				const getIndex = button => Number(button.getAttribute('aria-label').match(/slot (\d+)/)?.[1] || 99)
				return getIndex(a) - getIndex(b)
			})
	}

	function findEmptySlot(side) {
		return getSlots(side).find(button => button.innerText.trim() === 'Pick') || null
	}

	function hasHeroOnSide(side, heroName) {
		const target = normalize(heroName)
		return [...document.querySelectorAll(`button[aria-label^="Change ${side} slot "]`)].some(button =>
			[...button.querySelectorAll('img[alt]')].some(image => normalize(image.alt) === target),
		)
	}

	function setInputValue(input, value) {
		const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
		if (setter) setter.call(input, value)
		else input.value = value

		input.dispatchEvent(new Event('input', { bubbles: true }))
		input.dispatchEvent(new Event('change', { bubbles: true }))
	}

	async function findHeroButton(heroName) {
		const target = normalize(heroName)
		const search = [...document.querySelectorAll('input[placeholder="Search heroes..."]')].find(visible)

		if (!search) {
			debug('ERROR', 'Slot hero search not found', { heroName })
			return null
		}

		const picker = search.closest('div.fixed')
		if (!picker) {
			debug('ERROR', 'Slot hero picker not found', { heroName })
			return null
		}

		setInputValue(search, heroName)
		await sleep(300)

		const candidates = [...picker.querySelectorAll('button')].filter(button => {
			if (!visible(button)) return false

			const image = button.querySelector('img[alt]')
			if (!image || normalize(image.alt) !== target) return false

			const text = button.innerText || ''
			const lines = text
				.split(/\n|\r/)
				.map(line => line.trim())
				.filter(Boolean)

			return lines.some(line => normalize(line) === target)
		})

		debug(candidates.length ? 'INFO' : 'ERROR', 'Hero card search result', {
			heroName,
			count: candidates.length,
			matches: candidates.slice(0, 3).map(button => (button.innerText || '').trim().slice(0, 100)),
		})

		return candidates[0] || null
	}

	async function pickHero(side, heroName) {
		if (hasHeroOnSide(side, heroName)) {
			debug('INFO', 'Hero already placed on requested side', { side, heroName })
			return 'already-picked'
		}

		const slot = findEmptySlot(side)
		if (!slot) {
			debug('ERROR', 'No empty slot on requested side', { side, heroName })
			return 'no-empty-slot'
		}

		const slotLabel = slot.getAttribute('aria-label')
		debug('INFO', 'Opening target slot', { side, heroName, slotLabel })
		slot.click()
		await sleep(150)

		const heroButton = await findHeroButton(heroName)
		if (!heroButton) return 'not-found'

		const currentSlot = [...document.querySelectorAll('button[aria-label^="Pick hero for"]')].find(
			button => button.getAttribute('aria-label') === slotLabel && visible(button),
		)

		const activeLabel = document.querySelector('button[aria-current="true"][aria-label^="Pick hero for"]')?.getAttribute('aria-label') || null

		debug('INFO', 'Before hero click', {
			side,
			heroName,
			expectedSlot: slotLabel,
			slotStillExists: Boolean(currentSlot),
			activeLabel,
		})

		if (!currentSlot || activeLabel !== slotLabel) {
			debug('ERROR', 'Target slot is no longer active; stopping to avoid wrong-side pick', {
				side,
				heroName,
				expectedSlot: slotLabel,
				activeLabel,
			})
			return 'slot-changed'
		}

		debug('INFO', 'Clicked hero button details', {
			side,
			heroName,
			buttonText: heroButton.innerText,
			buttonHTML: heroButton.outerHTML.slice(0, 500),
			slotBeforeClick: document.querySelector('button[aria-current="true"][aria-label^="Pick hero for"]')?.getAttribute('aria-label') ?? null,
		})

		heroButton.click()
		await sleep(350)

		debug('INFO', 'DOM after hero click', {
			activeSlot: document.querySelector('button[aria-current="true"][aria-label^="Pick hero for"]')?.getAttribute('aria-label') ?? null,
			radiantSlots: getSlots('radiant').map(button => ({
				label: button.getAttribute('aria-label'),
				text: button.innerText.trim().slice(0, 60),
			})),
			direSlots: getSlots('dire').map(button => ({
				label: button.getAttribute('aria-label'),
				text: button.innerText.trim().slice(0, 60),
			})),
		})
		await sleep(150)

		const sideSlots = getSlots(side)
		const targetStillEmpty = sideSlots.some(button => button.getAttribute('aria-label') === slotLabel && button.innerText.trim() === 'Pick')
		const otherSide = side === 'radiant' ? 'dire' : 'radiant'
		const otherSlots = getSlots(otherSide)
		const otherSideChanged = otherSlots.some(button => button.innerText.trim() !== 'Pick')

		debug('INFO', 'After hero click', {
			side,
			heroName,
			targetSlotStillEmpty: targetStillEmpty,
			remainingEmptySlotsOnSide: sideSlots.filter(button => button.innerText.trim() === 'Pick').length,
			occupiedSlotsOnOtherSide: otherSlots
				.filter(button => button.innerText.trim() !== 'Pick')
				.map(button => ({
					label: button.getAttribute('aria-label'),
					text: button.innerText.trim().slice(0, 80),
				})),
		})

		if (targetStillEmpty) {
			debug('ERROR', 'Hero was not placed in requested slot', { side, heroName, slotLabel, otherSideChanged })
			return 'not-confirmed'
		}

		return 'picked'
	}

	async function applySide(side, heroes) {
		const results = []

		for (const rawName of heroes.slice(0, 5)) {
			const heroName = String(rawName || '').trim()
			if (!heroName) continue

			const result = await pickHero(side, heroName)
			results.push(`${side} ${heroName} — ${result}`)

			if (result !== 'picked' && result !== 'already-picked') break
		}

		return results
	}

	function readRecommendations() {
		const heading = [...document.querySelectorAll('span')].find(element => element.textContent.trim() === 'Recommended Picks')
		const section = heading?.parentElement?.parentElement
		if (!section) return null

		const sideButton = [...section.querySelectorAll('button')].find(
			button => ['Radiant', 'Dire'].includes(button.innerText.trim()) && button.getAttribute('aria-pressed') === 'true',
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

	async function syncRecommendations() {
		const result = readRecommendations()
		if (!result) return
		if (!result.items.length) {
			if (Date.now() - lastNoRecommendationsNoticeAt > 8000) {
				showStatus('D2PT is open, but no recommendation cards were found. Check the Recommended Picks section.')
				lastNoRecommendationsNoticeAt = Date.now()
			}
			return
		}
		lastNoRecommendationsNoticeAt = 0

		const signature = JSON.stringify(result)
		if (signature === lastRecommendationsSignature && Date.now() - lastRecommendationsSentAt < 3000) return

		try {
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

			if (!document.querySelector('button[aria-label="Pick hero for radiant slot 1"]')) {
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
				showStatus(`Applied ${picked} pick(s), then stopped to avoid incorrect picks.\n${failures.join('\n')}`)
				debug('ERROR', 'Draft application stopped', { results })
				return
			}

			lastPayloadSignature = signature
			showStatus(`Applied ${picked} pick(s); skipped ${skipped} already placed.`)
			debug('INFO', 'Draft application complete', { results })
		} catch (error) {
			debug('ERROR', 'Sync failed', { message: String(error), stack: error?.stack })
			showStatus('Bridge connection failed. Make sure it is running at 127.0.0.1:8765.')
		} finally {
			busy = false
		}
	}

	setInterval(sync, 1000)
	showStatus('Extension loaded. Waiting for local draft feed…')
})()
