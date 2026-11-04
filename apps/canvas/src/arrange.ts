import ELK from 'elkjs/lib/elk.bundled.js'
import { getNodesBounds, type Node } from '@xyflow/react'
import { toPng, toSvg } from 'html-to-image'

import type { Position, Relationship } from './api'

// Auto-layout (elkjs, layered: tables that point at others sit before them) and image export.

const elk = new ELK()

export type Direction = 'RIGHT' | 'DOWN'

/** New positions for the given tables, laid out from the top-left of where they are now. */
export async function arrange(
  nodes: Node[],
  relationships: Relationship[],
  nameOf: (id: string) => string,
  direction: Direction,
): Promise<Record<string, Position>> {
  const ids = new Set(nodes.map((n) => n.id))
  const byName = new Map(nodes.map((n) => [nameOf(n.id).toLowerCase(), n.id]))
  const edges = relationships.flatMap((r, i) => {
    const from = byName.get(r.from.toLowerCase())
    const to = byName.get(r.to.toLowerCase())
    // Facts point at dimensions: lay the dimension out first, so lines run one way.
    return from && to && from !== to && ids.has(from) && ids.has(to) ? [{ id: `e${i}`, sources: [to], targets: [from] }] : []
  })
  const graph = await elk.layout({
    id: 'root',
    layoutOptions: {
      'elk.algorithm': 'layered',
      'elk.direction': direction,
      'elk.spacing.nodeNode': '48',
      'elk.layered.spacing.nodeNodeBetweenLayers': '96',
      'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF',
      'elk.separateConnectedComponents': 'true',
      'elk.spacing.componentComponent': '80',
    },
    children: nodes.map((n) => ({ id: n.id, width: n.measured?.width ?? 280, height: n.measured?.height ?? 160 })),
    edges,
  })
  const left = Math.min(...nodes.map((n) => n.position.x))
  const top = Math.min(...nodes.map((n) => n.position.y))
  return Object.fromEntries(
    (graph.children ?? []).map((c) => [c.id, { x: Math.round(left + (c.x ?? 0)), y: Math.round(top + (c.y ?? 0)) }]),
  )
}

/** The canvas as a PNG or SVG file of everything shown, at full size, downloaded by the browser. */
export async function exportImage(nodes: Node[], format: 'png' | 'svg', fileName: string) {
  const viewport = document.querySelector<HTMLElement>('.react-flow__viewport')
  if (!viewport || nodes.length === 0) return
  const bounds = getNodesBounds(nodes)
  const padding = 40
  const width = Math.min(Math.ceil(bounds.width + 2 * padding), 8000)
  const height = Math.min(Math.ceil(bounds.height + 2 * padding), 8000)
  const background = getComputedStyle(document.body).getPropertyValue('--bg').trim() || '#ffffff'
  const options = {
    backgroundColor: background,
    width,
    height,
    pixelRatio: 2,
    // At full size, moved so the tables start at the padding.
    style: {
      width: `${width}px`,
      height: `${height}px`,
      transform: `translate(${padding - bounds.x}px, ${padding - bounds.y}px) scale(1)`,
    },
    // Leave out the handles (dots) that only matter while editing.
    filter: (node: HTMLElement) => !node.classList?.contains('react-flow__handle'),
  }
  // The crow's-foot ends are drawn from definitions outside the viewport: copy them in for the
  // export, or the lines end bare. (html-to-image stays at 1.11.11: later versions drop the lines.)
  const markers = document.querySelector('svg.markers')?.cloneNode(true) as SVGElement | undefined
  if (markers) viewport.prepend(markers)
  let url: string
  try {
    url = format === 'png' ? await toPng(viewport, options) : await toSvg(viewport, options)
  } finally {
    markers?.remove()
  }
  const link = document.createElement('a')
  link.download = `${fileName}.${format}`
  link.href = url
  link.click()
}
