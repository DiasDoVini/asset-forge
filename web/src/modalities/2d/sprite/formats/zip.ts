// Leitor de ZIP mínimo (store e deflate) para .pxo, .kra e outros contêineres. Sem dependências: usa
// DecompressionStream('deflate-raw') do navegador.

export type ZipEntry = { name: string; method: number; csize: number; usize: number; offset: number }

export class ZipError extends Error {}

export function listZip(buf: ArrayBuffer): Map<string, ZipEntry> {
  const dv = new DataView(buf)
  let eocd = -1
  for (let i = buf.byteLength - 22; i >= Math.max(0, buf.byteLength - 65557); i--)
    if (dv.getUint32(i, true) === 0x06054b50) {
      eocd = i
      break
    }
  if (eocd < 0) throw new ZipError('não é um arquivo ZIP')
  const n = dv.getUint16(eocd + 10, true)
  let p = dv.getUint32(eocd + 16, true)
  const out = new Map<string, ZipEntry>()
  const td = new TextDecoder()
  for (let k = 0; k < n; k++) {
    if (dv.getUint32(p, true) !== 0x02014b50) throw new ZipError('diretório central inválido')
    const method = dv.getUint16(p + 10, true)
    const csize = dv.getUint32(p + 20, true)
    const usize = dv.getUint32(p + 24, true)
    const nlen = dv.getUint16(p + 28, true)
    const elen = dv.getUint16(p + 30, true)
    const clen = dv.getUint16(p + 32, true)
    const offset = dv.getUint32(p + 42, true)
    const name = td.decode(new Uint8Array(buf, p + 46, nlen))
    out.set(name, { name, method, csize, usize, offset })
    p += 46 + nlen + elen + clen
  }
  return out
}

export async function readZipEntry(buf: ArrayBuffer, e: ZipEntry): Promise<Uint8Array> {
  const dv = new DataView(buf)
  if (dv.getUint32(e.offset, true) !== 0x04034b50) throw new ZipError(`cabeçalho local inválido: ${e.name}`)
  const start = e.offset + 30 + dv.getUint16(e.offset + 26, true) + dv.getUint16(e.offset + 28, true)
  const data = new Uint8Array(buf, start, e.csize)
  if (e.method === 0) return data.slice()
  if (e.method !== 8) throw new ZipError(`compressão ${e.method} não suportada (${e.name})`)
  const stream = new Blob([data as BlobPart]).stream().pipeThrough(new DecompressionStream('deflate-raw'))
  return new Uint8Array(await new Response(stream).arrayBuffer())
}

export async function readZipText(buf: ArrayBuffer, entries: Map<string, ZipEntry>, name: string): Promise<string | null> {
  const e = entries.get(name)
  return e ? new TextDecoder().decode(await readZipEntry(buf, e)) : null
}
