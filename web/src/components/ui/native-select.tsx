import * as React from "react"
import { ChevronDown } from "lucide-react"
import { cn } from "@/lib/utils"

/** <select> nativo com o visual dos outros controles (altura casando com Button size sm/default). */
function NativeSelect({
  className,
  size = "default",
  wrapperClassName,
  ...props
}: Omit<React.ComponentProps<"select">, "size"> & { size?: "sm" | "default"; wrapperClassName?: string }) {
  return (
    <div className={cn("relative inline-flex", wrapperClassName)}>
      <select
        data-slot="native-select"
        className={cn(
          "w-full min-w-0 appearance-none rounded-lg border border-input bg-transparent pr-7 pl-2 outline-none transition-colors focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 dark:bg-input/30 [&>option]:bg-popover [&>option]:text-popover-foreground",
          size === "sm" ? "h-7 text-[0.8rem]" : "h-8 text-sm",
          className
        )}
        {...props}
      />
      <ChevronDown aria-hidden className="pointer-events-none absolute top-1/2 right-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
    </div>
  )
}

export { NativeSelect }
