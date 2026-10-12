import {Group, Rect, Text} from 'react-konva'

export function RegionLabel({x, y, text, color, scale}: {x: number, y: number, text: string, color: string, scale: number}) {
  const width = [...text].reduce((total, character) => total + (character.codePointAt(0)! > 127 ? 12 : 7), 12)
  return <Group x={x} y={Math.max(0, y - 25 * scale)} scaleX={scale} scaleY={scale} listening={false}>
    <Rect width={width} height={21} fill="rgba(255,255,255,.96)" stroke={color} strokeWidth={1} cornerRadius={4}/>
    <Text x={6} y={4} text={text} fontFamily="-apple-system, BlinkMacSystemFont, Segoe UI, Microsoft YaHei, sans-serif" fontSize={12} fill={color}/>
  </Group>
}
