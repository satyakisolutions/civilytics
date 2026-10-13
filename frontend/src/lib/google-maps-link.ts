// Parse latitude/longitude out of a pasted Google Maps URL (or bare "lat,lng").
// Pure, client-side only — short links (maps.app.goo.gl / goo.gl/maps) can't be
// resolved here because expanding them needs a redirect that CORS blocks.

export function isGoogleMapsShortLink(url: string): boolean {
  return /\b(maps\.app\.goo\.gl|goo\.gl\/maps)\b/i.test(url);
}

const inRange = (lat: number, lng: number): boolean =>
  lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180;

export function parseGoogleMapsLatLng(url: string): { lat: number; lng: number } | null {
  if (!url) return null;
  const s = url.trim();

  // Patterns tried in order; each capture group 1 = lat, group 2 = lng.
  const patterns: RegExp[] = [
    /@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),[\d.]+z/i,          // @lat,lng,15z
    /!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)/i,              // place !3d..!4d..
    /[?&](?:q|query|ll)=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/i, // ?q= / query= / ll=
    /^(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)$/,               // bare "lat,lng"
  ];

  for (const re of patterns) {
    const m = s.match(re);
    if (m) {
      const lat = parseFloat(m[1]);
      const lng = parseFloat(m[2]);
      if (!Number.isNaN(lat) && !Number.isNaN(lng) && inRange(lat, lng)) {
        return { lat, lng };
      }
    }
  }
  return null;
}
