import { useEffect, useRef, useMemo, useCallback, useState } from 'react'
import Globe from 'react-globe.gl'
import type { WeatherForecast, WeatherSignal } from '../types'

interface Props {
  forecasts: WeatherForecast[]
  signals: WeatherSignal[]
}

interface CityMarker {
  lat: number
  lng: number
  name: string
  key: string
  forecast: WeatherForecast | null
  bestSignal: WeatherSignal | null
  hasActionable: boolean
}

const CITIES: Record<string, { lat: number; lng: number; name: string }> = {
  nyc: { lat: 40.7772, lng: -73.8726, name: 'NYC' },
  chicago: { lat: 41.9742, lng: -87.9073, name: 'CHI' },
  miami: { lat: 25.7959, lng: -80.2870, name: 'MIA' },
  dallas: { lat: 32.8471, lng: -96.8518, name: 'DAL' },
  seattle: { lat: 47.4502, lng: -122.3088, name: 'SEA' },
  atlanta: { lat: 33.6407, lng: -84.4277, name: 'ATL' },
  london: { lat: 51.5048, lng: 0.0495, name: 'LON' },
  paris: { lat: 48.9962, lng: 2.5979, name: 'PAR' },
  munich: { lat: 48.3537, lng: 11.7750, name: 'MUC' },
  ankara: { lat: 40.1281, lng: 32.9951, name: 'ANK' },
  seoul: { lat: 37.4691, lng: 126.4505, name: 'SEL' },
  tokyo: { lat: 35.7647, lng: 140.3864, name: 'TYO' },
  shanghai: { lat: 31.1443, lng: 121.8083, name: 'SHA' },
  singapore: { lat: 1.3502, lng: 103.9940, name: 'SIN' },
  lucknow: { lat: 26.7606, lng: 80.8893, name: 'LKO' },
  'tel-aviv': { lat: 32.0114, lng: 34.8867, name: 'TLV' },
  toronto: { lat: 43.6772, lng: -79.6306, name: 'TOR' },
  'sao-paulo': { lat: -23.4356, lng: -46.4731, name: 'SAO' },
  'buenos-aires': { lat: -34.8222, lng: -58.5358, name: 'BUE' },
  wellington: { lat: -41.3272, lng: 174.8052, name: 'WLG' },
}

export function GlobeView({ forecasts, signals }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const globeRef = useRef<any>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })

  const markers: CityMarker[] = useMemo(() => {
    const signalGap = (signal: WeatherSignal) => Math.abs(signal.probability_edge ?? signal.edge ?? 0)
    return Object.entries(CITIES).map(([key, city]) => {
      const forecast = forecasts.find(f => f.city_key === key) || null
      const citySignals = signals.filter(s => s.city_key === key)
      const actionableSignals = citySignals.filter(s => s.actionable)
      const bestSignal = actionableSignals.length > 0
        ? actionableSignals.reduce((a, b) => signalGap(a) > signalGap(b) ? a : b)
        : citySignals.length > 0
          ? citySignals.reduce((a, b) => signalGap(a) > signalGap(b) ? a : b)
          : null

      return {
        lat: city.lat,
        lng: city.lng,
        name: city.name,
        key,
        forecast,
        bestSignal,
        hasActionable: actionableSignals.length > 0,
      }
    })
  }, [forecasts, signals])

  useEffect(() => {
    if (globeRef.current) {
      const controls = globeRef.current.controls()
      globeRef.current.pointOfView({ lat: 14, lng: 18, altitude: 1.65 }, 800)
      controls.autoRotate = true
      controls.autoRotateSpeed = 0.25
      controls.enableZoom = true
      controls.minDistance = 115
      controls.maxDistance = 430
    }
  }, [size.width, size.height])

  useEffect(() => {
    if (!containerRef.current) return

    const updateSize = () => {
      if (!containerRef.current) return
      const rect = containerRef.current.getBoundingClientRect()
      setSize({
        width: Math.max(240, Math.floor(rect.width)),
        height: Math.max(240, Math.floor(rect.height)),
      })
    }

    updateSize()
    const observer = new ResizeObserver(updateSize)
    observer.observe(containerRef.current)
    return () => observer.disconnect()
  }, [])

  const handleInteraction = useCallback(() => {
    if (globeRef.current) {
      globeRef.current.controls().autoRotate = false
      setTimeout(() => {
        if (globeRef.current) {
          globeRef.current.controls().autoRotate = true
        }
      }, 5000)
    }
  }, [])

  const markerElement = useCallback((d: object) => {
    const marker = d as CityMarker
    const el = document.createElement('div')
    el.className = 'city-marker'

    const dotColor = marker.hasActionable ? '#22c55e' : marker.bestSignal ? '#d97706' : '#525252'

    const dot = document.createElement('div')
    dot.className = 'marker-dot'
    dot.style.backgroundColor = dotColor
    dot.style.color = dotColor
    el.appendChild(dot)

    const label = document.createElement('div')
    label.className = 'marker-label'

    const nameSpan = document.createElement('div')
    nameSpan.className = 'marker-name'
    nameSpan.textContent = marker.name
    label.appendChild(nameSpan)

    if (marker.forecast) {
      const tempSpan = document.createElement('div')
      tempSpan.className = 'marker-temp'
      tempSpan.style.color = '#e5e5e5'
      tempSpan.textContent = `${marker.forecast.mean_high.toFixed(0)}F`
      label.appendChild(tempSpan)
    }

    if (marker.bestSignal?.actionable) {
      const edgeSpan = document.createElement('div')
      edgeSpan.className = 'marker-edge'
      const probabilityGap = marker.bestSignal.probability_edge ?? marker.bestSignal.edge
      const edgeVal = (probabilityGap * 100).toFixed(1)
      edgeSpan.style.color = probabilityGap > 0 ? '#22c55e' : '#dc2626'
      edgeSpan.textContent = `Δ${probabilityGap > 0 ? '+' : ''}${edgeVal}pp`
      label.appendChild(edgeSpan)
    }

    el.appendChild(label)
    return el
  }, [])

  return (
    <div ref={containerRef} className="globe-container w-full h-full">
      {size.width > 0 && size.height > 0 && (
        <Globe
          ref={globeRef}
          globeImageUrl="//unpkg.com/three-globe/example/img/earth-night.jpg"
          backgroundColor="rgba(0,0,0,0)"
          atmosphereColor="#1a1a2e"
          atmosphereAltitude={0.15}
          htmlElementsData={markers}
          htmlElement={markerElement}
          htmlAltitude={0.01}
          onGlobeClick={handleInteraction}
          onZoom={handleInteraction}
          width={size.width}
          height={size.height}
        />
      )}
    </div>
  )
}
