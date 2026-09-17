import type { Geometry } from "../../api/gis";

export type Position = [number, number];
export interface PolygonGeometry extends Geometry { type: "Polygon"; coordinates: Position[][]; }

const EARTH_AUTHALIC_RADIUS_METRES = 6_371_007.1809;
const SQUARE_FEET_PER_SQUARE_METRE = 10.7639104167;

export function cloneGeometry<T extends Geometry>(geometry: T): T {
  return structuredClone(geometry);
}

export function isEditablePolygon(geometry: Geometry | null): geometry is PolygonGeometry {
  return geometry?.type === "Polygon" && Array.isArray(geometry.coordinates) && geometry.coordinates.length === 1;
}

export function closedRing(ring: Position[]): Position[] {
  if (!ring.length) return [];
  const first = ring[0]; const last = ring[ring.length - 1];
  return first[0] === last[0] && first[1] === last[1] ? ring : [...ring, [...first] as Position];
}

export function openRing(geometry: PolygonGeometry): Position[] {
  const ring = geometry.coordinates[0] ?? [];
  return ring.length > 1 ? ring.slice(0, -1).map(([longitude, latitude]) => [longitude, latitude]) : [];
}

export function polygonFromOpenRing(points: Position[]): PolygonGeometry {
  return { type: "Polygon", coordinates: [closedRing(points.map(([longitude, latitude]) => [longitude, latitude]))] };
}

export function replaceVertex(geometry: PolygonGeometry, index: number, position: Position): PolygonGeometry {
  const points = openRing(geometry); points[index] = position; return polygonFromOpenRing(points);
}

export function insertVertex(geometry: PolygonGeometry, index: number, position: Position): PolygonGeometry {
  const points = openRing(geometry); points.splice(index + 1, 0, position); return polygonFromOpenRing(points);
}

export function removeVertex(geometry: PolygonGeometry, index: number): PolygonGeometry | null {
  const points = openRing(geometry); if (points.length <= 3) return null; points.splice(index, 1); return polygonFromOpenRing(points);
}

export function nearestEdgeIndex(geometry: PolygonGeometry, position: Position): number {
  const points = openRing(geometry); let nearest = 0; let distance = Number.POSITIVE_INFINITY;
  for (let index = 0; index < points.length; index += 1) {
    const start = points[index]; const end = points[(index + 1) % points.length];
    const candidate = squaredDistanceToSegment(position, start, end);
    if (candidate < distance) { distance = candidate; nearest = index; }
  }
  return nearest;
}

function squaredDistanceToSegment(point: Position, start: Position, end: Position): number {
  const dx = end[0] - start[0]; const dy = end[1] - start[1];
  const scale = dx * dx + dy * dy;
  const ratio = scale === 0 ? 0 : Math.max(0, Math.min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / scale));
  const x = start[0] + ratio * dx; const y = start[1] + ratio * dy;
  return (point[0] - x) ** 2 + (point[1] - y) ** 2;
}

export function basicPolygonProblems(geometry: PolygonGeometry): string[] {
  const rawRing = geometry.coordinates[0] ?? [];
  const rawFirst = rawRing[0]; const rawLast = rawRing[rawRing.length - 1];
  if (!rawFirst || !rawLast || rawFirst[0] !== rawLast[0] || rawFirst[1] !== rawLast[1]) return ["A polygon ring must be closed before it can be saved."];
  const points = openRing(geometry);
  if (points.length < 3) return ["A parcel boundary needs at least three vertices."];
  if (points.some(([longitude, latitude]) => !Number.isFinite(longitude) || !Number.isFinite(latitude) || longitude < -180 || longitude > 180 || latitude < -90 || latitude > 90)) return ["Each vertex must contain valid longitude and latitude coordinates."];
  const unique = new Set(points.map(([longitude, latitude]) => `${longitude}:${latitude}`));
  if (unique.size < 3) return ["A parcel boundary needs three distinct vertices."];
  return [];
}

export function geodesicAreaM2(geometry: PolygonGeometry): number | null {
  if (basicPolygonProblems(geometry).length) return null;
  const ring = closedRing(openRing(geometry)); let sum = 0;
  for (let index = 0; index < ring.length - 1; index += 1) {
    const [longitudeA, latitudeA] = ring[index]; const [longitudeB, latitudeB] = ring[index + 1];
    sum += toRadians(longitudeB - longitudeA) * (2 + Math.sin(toRadians(latitudeA)) + Math.sin(toRadians(latitudeB)));
  }
  return Math.abs(sum) * EARTH_AUTHALIC_RADIUS_METRES ** 2 / 2;
}

export function squareFeet(areaM2: number | null): number | null { return areaM2 === null ? null : areaM2 * SQUARE_FEET_PER_SQUARE_METRE; }
export function percentDifference(before: number | null, after: number | null): number | null { return before === null || after === null || before === 0 ? null : ((after - before) / before) * 100; }
const toRadians = (degrees: number) => degrees * Math.PI / 180;
