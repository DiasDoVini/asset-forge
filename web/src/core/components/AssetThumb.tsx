import { FileBox } from 'lucide-react'
import type { AssetRow } from '../api'
import { getModality } from '../registry'
import { checker } from './ImageViewer'
import { cn } from '@/lib/utils'

export function AssetThumb({ asset, className }: { asset: AssetRow; className?: string }) {
  const Thumb = getModality(asset.modality)?.thumbnail
  const img = asset.preview_url && /\.(png|jpe?g|webp|gif)$/i.test(asset.preview_url) ? asset.preview_url : null
  return (
    <div className={cn('asset-thumb flex items-center justify-center overflow-hidden', checker, className)}>
      {img ? (
        <img
          src={img}
          alt={asset.name}
          loading="lazy"
          className="max-h-full max-w-full object-contain"
          style={{ imageRendering: asset.kind === 'sprite' ? 'pixelated' : 'auto' }}
        />
      ) : Thumb && asset.primary_url ? (
        <Thumb url={asset.primary_url} asset={asset} />
      ) : (
        <div className="flex flex-col items-center gap-1 text-muted-foreground">
          <FileBox className="size-8" />
          <span className="text-xs">{asset.primary_file?.split('.').pop()}</span>
        </div>
      )}
    </div>
  )
}
