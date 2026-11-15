// Títulos dos pipelines em português na UI (o servidor ainda manda alguns em inglês; o id continua o mesmo).
const TITLES: Record<string, string> = {
  '2d.text2d': 'Texto → 2D',
  '2d.text2pixel': 'Texto → pixel art',
  '2d.checkpoint': 'Texto → 2D (checkpoint)',
  '2d.image2d': 'Imagem → 2D',
  '2d.edit': 'Editar 2D',
  '2d.enhance': 'Melhorar / ampliar 2D',
  '2d.pixelize': 'Pixelizar',
  '3d.text_to_3d': 'Texto → 3D',
  '3d.image_to_3d': 'Imagem → 3D',
  '3d.from_raw': 'Refazer a partir do bruto',
  '3d.edit': 'Editar malha',
  '3d.ai_edit': 'Editar com IA',
  '3d.refine_region': 'Refinar região',
  '3d.unwrap': 'Preparar para pintura (UV)',
  '3d.texture_ai': 'Textura com IA',
  '3d.apply_texture': 'Aplicar texturas pintadas',
  '3d.bake_pbr': 'Mapas PBR',
  '3d.rig': 'Rig por partes',
  '3d.skeleton': 'Esqueleto (skinning)',
  '3d.animate': 'Animar',
  '3d.character': 'Personagem → animável (IA)',
  '3d.character_rig': 'Rig de personagem + animações',
  '3d.retarget': 'Animações da biblioteca',
  '3d.simplify': 'Simplificar',
  '3d.lowpoly': 'Low poly',
  '3d.repair': 'Reparar',
  '3d.transform': 'Transformar',
  '3d.convert': 'Converter',
  '3d.print': 'Otimizar para impressão',
  '3d.split': 'Dividir para impressão',
  'audio.sfx': 'Texto → efeito sonoro',
  'audio.music': 'Texto → música',
  'pixel.map': 'Pixel map → sprite',
  'vfx.shader': 'Texto → shader',
  'vfx.particles': 'Texto → partículas',
  'video.generate': 'Texto → vídeo',
}

export function pipelineTitle(p: { id: string; title: string } | string, fallback?: string): string {
  if (typeof p === 'string') return TITLES[p] ?? fallback ?? p
  return TITLES[p.id] ?? p.title
}
