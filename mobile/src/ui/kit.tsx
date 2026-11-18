/** The building blocks every screen is made of. */

import { Ionicons } from '@expo/vector-icons';
import { useIsFocused } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import type { ComponentProps, ReactNode } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Switch, Text, TextInput, View } from 'react-native';
import type { StyleProp, TextInputProps, TextStyle, ViewStyle } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { radius, space, useTheme } from './theme';

export type IconName = ComponentProps<typeof Ionicons>['name'];

/**
 * Status bar icons for the screen that is in front. Screens underneath stay mounted, so each
 * one only speaks while it is the focused one. light: white icons, for the gradient screens.
 */
export function Bar({ light }: { light?: boolean }) {
  const t = useTheme();
  return useIsFocused() ? <StatusBar style={light || t.dark ? 'light' : 'dark'} /> : null;
}

export function Screen({ children, scroll = true, footer, padTop = false }: {
  children: ReactNode; scroll?: boolean; footer?: ReactNode; padTop?: boolean;
}) {
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const pad = { paddingHorizontal: space.lg, paddingTop: space.lg,
                paddingBottom: space.xl + (footer ? 0 : insets.bottom) };
  return (
    <View style={{ flex: 1, backgroundColor: t.bg }}>
      <Bar />
      {/* a fixed strip, so scrolled content never shows through the status bar */}
      {padTop ? <View style={{ height: insets.top, backgroundColor: t.bg }} /> : null}
      {scroll ? (
        <ScrollView contentContainerStyle={pad} keyboardShouldPersistTaps="handled">{children}</ScrollView>
      ) : (
        <View style={[{ flex: 1 }, pad]}>{children}</View>
      )}
      {footer ? (
        <View style={{ paddingHorizontal: space.lg, paddingTop: space.md, paddingBottom: insets.bottom + space.md,
                       backgroundColor: t.bg, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.border }}>
          {footer}
        </View>
      ) : null}
    </View>
  );
}

export function H1({ children, style }: { children: ReactNode; style?: StyleProp<TextStyle> }) {
  const t = useTheme();
  return <Text accessibilityRole="header" style={[{ color: t.text, fontSize: 28, fontWeight: '700', letterSpacing: -0.4 }, style]}>{children}</Text>;
}

export function H2({ children, style }: { children: ReactNode; style?: StyleProp<TextStyle> }) {
  const t = useTheme();
  return <Text accessibilityRole="header" style={[{ color: t.text, fontSize: 18, fontWeight: '700' }, style]}>{children}</Text>;
}

export function P({ children, muted, small, style, selectable, numberOfLines, testID }: {
  children: ReactNode; muted?: boolean; small?: boolean; style?: StyleProp<TextStyle>; selectable?: boolean;
  numberOfLines?: number; testID?: string;
}) {
  const t = useTheme();
  return (
    <Text testID={testID} selectable={selectable} numberOfLines={numberOfLines}
          style={[{ color: muted ? t.muted : t.text, fontSize: small ? 13 : 15, lineHeight: small ? 18 : 22 }, style]}>
      {children}
    </Text>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  const t = useTheme();
  return (
    <Text accessibilityRole="header"
          style={{ color: t.faint, fontSize: 12, fontWeight: '700', letterSpacing: 0.8, textTransform: 'uppercase',
                   marginTop: space.xl, marginBottom: space.sm, marginLeft: space.xs }}>
      {children}
    </Text>
  );
}

export function Card({ children, style, padded = true }: { children: ReactNode; style?: StyleProp<ViewStyle>; padded?: boolean }) {
  const t = useTheme();
  return (
    <View style={[{ backgroundColor: t.card, borderRadius: radius.lg, borderWidth: StyleSheet.hairlineWidth,
                    borderColor: t.border, padding: padded ? space.lg : 0, overflow: 'hidden' }, style]}>
      {children}
    </View>
  );
}

export function Button({ title, onPress, kind = 'primary', icon, busy, disabled, testID, style }: {
  title: string; onPress: () => void; kind?: 'primary' | 'secondary' | 'danger' | 'ghost'; icon?: IconName;
  busy?: boolean; disabled?: boolean; testID?: string; style?: StyleProp<ViewStyle>;
}) {
  const t = useTheme();
  const off = disabled || busy;
  const bg = kind === 'primary' ? t.primary : kind === 'danger' ? t.badSoft : kind === 'secondary' ? t.primarySoft : 'transparent';
  const fg = kind === 'primary' ? t.onPrimary : kind === 'danger' ? t.bad : t.primary;
  return (
    <Pressable testID={testID} accessibilityRole="button" accessibilityLabel={title} accessibilityState={{ disabled: !!off, busy: !!busy }}
               disabled={off} onPress={onPress}
               style={({ pressed }) => [{ minHeight: 50, borderRadius: radius.md, backgroundColor: bg, flexDirection: 'row',
                                          alignItems: 'center', justifyContent: 'center', paddingHorizontal: space.lg,
                                          opacity: off ? 0.5 : pressed ? 0.8 : 1 }, style]}>
      {busy ? <ActivityIndicator color={fg} style={{ marginRight: space.sm }} /> :
        icon ? <Ionicons name={icon} size={19} color={fg} style={{ marginRight: space.sm }} /> : null}
      <Text style={{ color: fg, fontSize: 16, fontWeight: '600' }}>{title}</Text>
    </Pressable>
  );
}

/** One line of a list inside a Card: icon, title, detail, and something on the right. */
export function Row({ icon, title, detail, right, onPress, first, danger, testID }: {
  icon?: IconName; title: string; detail?: string | null; right?: ReactNode; onPress?: () => void; first?: boolean;
  danger?: boolean; testID?: string;
}) {
  const t = useTheme();
  const body = (
    <View style={{ flexDirection: 'row', alignItems: 'center', paddingVertical: space.md, paddingHorizontal: space.lg,
                   minHeight: 56, borderTopWidth: first ? 0 : StyleSheet.hairlineWidth, borderTopColor: t.border }}>
      {icon ? (
        <View style={{ width: 34, height: 34, borderRadius: 10, backgroundColor: danger ? t.badSoft : t.primarySoft,
                       alignItems: 'center', justifyContent: 'center', marginRight: space.md }}>
          <Ionicons name={icon} size={18} color={danger ? t.bad : t.primary} />
        </View>
      ) : null}
      <View style={{ flex: 1 }}>
        <Text style={{ color: danger ? t.bad : t.text, fontSize: 16, fontWeight: '500' }}>{title}</Text>
        {detail ? <Text style={{ color: t.muted, fontSize: 13, lineHeight: 18, marginTop: 2 }}>{detail}</Text> : null}
      </View>
      {right !== undefined ? <View style={{ marginLeft: space.md }}>{right}</View> :
        onPress ? <Ionicons name="chevron-forward" size={18} color={t.faint} style={{ marginLeft: space.sm }} /> : null}
    </View>
  );
  if (!onPress) return <View testID={testID}>{body}</View>;
  return (
    <Pressable testID={testID} accessibilityRole="button" accessibilityLabel={title} onPress={onPress}
               style={({ pressed }) => ({ backgroundColor: pressed ? t.cardAlt : 'transparent' })}>
      {body}
    </Pressable>
  );
}

export function ToggleRow({ icon, title, detail, value, onChange, first, disabled, testID }: {
  icon?: IconName; title: string; detail?: string | null; value: boolean; onChange: (v: boolean) => void;
  first?: boolean; disabled?: boolean; testID?: string;
}) {
  const t = useTheme();
  return (
    <Row icon={icon} title={title} detail={detail} first={first}
         right={<Switch testID={testID} accessibilityLabel={title} value={value} onValueChange={onChange} disabled={disabled}
                        trackColor={{ true: t.primary, false: t.border }} thumbColor="#FFFFFF" />} />
  );
}

export function Notice({ kind = 'info', title, children, action, onAction, testID }: {
  kind?: 'info' | 'good' | 'warn' | 'bad'; title?: string; children?: ReactNode; action?: string; onAction?: () => void;
  testID?: string;
}) {
  const t = useTheme();
  const colors = { info: [t.primarySoft, t.primary], good: [t.goodSoft, t.good], warn: [t.warnSoft, t.warn],
                   bad: [t.badSoft, t.bad] }[kind];
  const icons: Record<string, IconName> = { info: 'information-circle', good: 'checkmark-circle', warn: 'alert-circle',
                                            bad: 'close-circle' };
  return (
    <View testID={testID} accessibilityRole={kind === 'bad' || kind === 'warn' ? 'alert' : undefined}
          style={{ backgroundColor: colors[0], borderRadius: radius.md, padding: space.md, flexDirection: 'row' }}>
      <Ionicons name={icons[kind]} size={20} color={colors[1]} style={{ marginRight: space.sm, marginTop: 1 }} />
      <View style={{ flex: 1 }}>
        {title ? <Text style={{ color: t.text, fontSize: 15, fontWeight: '600', marginBottom: children ? 2 : 0 }}>{title}</Text> : null}
        {typeof children === 'string' ? <Text style={{ color: t.text, fontSize: 14, lineHeight: 20 }}>{children}</Text> : children}
        {action && onAction ? (
          <Pressable accessibilityRole="button" onPress={onAction} hitSlop={8} style={{ marginTop: space.sm }}>
            <Text style={{ color: colors[1], fontSize: 15, fontWeight: '600' }}>{action}</Text>
          </Pressable>
        ) : null}
      </View>
    </View>
  );
}

export function ProgressBar({ value, color, track }: { value: number | null; color?: string; track?: string }) {
  const t = useTheme();
  const pct = value === null ? 100 : Math.max(2, Math.min(100, value * 100));
  return (
    <View accessibilityRole="progressbar" accessibilityValue={value === null ? undefined : { min: 0, max: 100, now: Math.round(pct) }}
          style={{ height: 8, borderRadius: 4, backgroundColor: track || t.border, overflow: 'hidden' }}>
      <View style={{ width: `${pct}%`, height: 8, borderRadius: 4, backgroundColor: color || t.primary,
                     opacity: value === null ? 0.45 : 1 }} />
    </View>
  );
}

export function Field({ label, hint, error, ...input }: TextInputProps & { label: string; hint?: string; error?: string | null }) {
  const t = useTheme();
  return (
    <View style={{ marginBottom: space.lg }}>
      <Text style={{ color: t.text, fontSize: 14, fontWeight: '600', marginBottom: 6 }}>{label}</Text>
      <TextInput accessibilityLabel={label} placeholderTextColor={t.faint} autoCorrect={false} {...input}
                 style={[{ backgroundColor: t.card, color: t.text, borderWidth: 1, borderColor: error ? t.bad : t.border,
                           borderRadius: radius.md, paddingHorizontal: space.md, paddingVertical: 13, fontSize: 16 }, input.style]} />
      {error ? <Text style={{ color: t.bad, fontSize: 13, marginTop: 6 }}>{error}</Text> :
        hint ? <Text style={{ color: t.muted, fontSize: 13, lineHeight: 18, marginTop: 6 }}>{hint}</Text> : null}
    </View>
  );
}

export function Check({ checked, onChange, label, testID }: { checked: boolean; onChange: (v: boolean) => void; label: string; testID?: string }) {
  const t = useTheme();
  return (
    <Pressable testID={testID} accessibilityRole="checkbox" accessibilityState={{ checked }} accessibilityLabel={label}
               onPress={() => onChange(!checked)} style={{ flexDirection: 'row', alignItems: 'center', paddingVertical: space.sm }}>
      <View style={{ width: 26, height: 26, borderRadius: 8, borderWidth: 2, borderColor: checked ? t.primary : t.faint,
                     backgroundColor: checked ? t.primary : 'transparent', alignItems: 'center', justifyContent: 'center',
                     marginRight: space.md }}>
        {checked ? <Ionicons name="checkmark" size={18} color={t.onPrimary} /> : null}
      </View>
      <Text style={{ color: t.text, fontSize: 15, lineHeight: 21, flex: 1 }}>{label}</Text>
    </Pressable>
  );
}

export function Loading({ text }: { text?: string }) {
  const t = useTheme();
  return (
    <View style={{ alignItems: 'center', paddingVertical: space.xxl }}>
      <ActivityIndicator color={t.primary} size="large" />
      {text ? <Text style={{ color: t.muted, marginTop: space.md, fontSize: 15 }}>{text}</Text> : null}
    </View>
  );
}

export function Empty({ icon, title, text }: { icon: IconName; title: string; text?: string }) {
  const t = useTheme();
  return (
    <View style={{ alignItems: 'center', paddingVertical: space.xxl, paddingHorizontal: space.xl }}>
      <View style={{ width: 72, height: 72, borderRadius: 36, backgroundColor: t.primarySoft, alignItems: 'center',
                     justifyContent: 'center', marginBottom: space.lg }}>
        <Ionicons name={icon} size={32} color={t.primary} />
      </View>
      <Text style={{ color: t.text, fontSize: 18, fontWeight: '700', textAlign: 'center' }}>{title}</Text>
      {text ? <Text style={{ color: t.muted, fontSize: 15, lineHeight: 21, textAlign: 'center', marginTop: space.sm }}>{text}</Text> : null}
    </View>
  );
}
