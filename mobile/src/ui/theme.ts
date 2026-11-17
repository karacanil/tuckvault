import { useColorScheme } from 'react-native';

/** The brand: the teal-to-blue of the Tuckvault icon. */
export const BRAND_TOP = '#14B8A6';
export const BRAND_BOTTOM = '#1D4ED8';

export type Theme = {
  dark: boolean;
  bg: string;
  card: string;
  cardAlt: string;
  text: string;
  muted: string;
  faint: string;
  border: string;
  primary: string;
  onPrimary: string;
  primarySoft: string;
  good: string;
  goodSoft: string;
  warn: string;
  warnSoft: string;
  bad: string;
  badSoft: string;
};

const light: Theme = {
  dark: false,
  bg: '#F3F6FA',
  card: '#FFFFFF',
  cardAlt: '#F0F4F9',
  text: '#0F1B2D',
  muted: '#55637A',
  faint: '#8894A7',
  border: '#E1E7F0',
  primary: '#1A63C9',
  onPrimary: '#FFFFFF',
  primarySoft: '#E5EEFB',
  good: '#0F8A6B',
  goodSoft: '#E0F5EE',
  warn: '#A8650B',
  warnSoft: '#FDF1DC',
  bad: '#C0352B',
  badSoft: '#FCE8E6',
};

const dark: Theme = {
  dark: true,
  bg: '#0B121C',
  card: '#141E2C',
  cardAlt: '#1A2637',
  text: '#EAF0F8',
  muted: '#A3B1C6',
  faint: '#73829A',
  border: '#243247',
  primary: '#5B9BF0',
  onPrimary: '#07142A',
  primarySoft: '#17294A',
  good: '#3CCBA4',
  goodSoft: '#0F2E29',
  warn: '#F0B354',
  warnSoft: '#33260F',
  bad: '#F0766C',
  badSoft: '#3A1917',
};

export function useTheme(): Theme {
  return useColorScheme() === 'dark' ? dark : light;
}

export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 };
export const radius = { sm: 10, md: 14, lg: 20 };
