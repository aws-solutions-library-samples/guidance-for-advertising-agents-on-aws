/**
 * Utility class for text manipulation and formatting
 */
export class TextUtils {
  /**
   * Remove stack prefix and suffix from agent names
   * @param name - The agent name that may contain stack prefix/suffix
   * @param stackPrefix - The stack prefix to remove (e.g., 'sim', 'demo3')
   * @param stackSuffix - The stack suffix to remove (e.g., '1234', 'abc123')
   * @returns Clean agent name without stack prefix/suffix
   */
  static removeStackPrefixSuffix(name: string, stackPrefix: string = '', stackSuffix: string = ''): string {
    if (!name) return '';

    let cleanName = name;

    // Remove stack prefix (case-insensitive, with optional separators)
    if (stackPrefix) {
      const prefixPattern = new RegExp(`^${stackPrefix}[-_\\s]*`, 'i');
      cleanName = cleanName.replace(prefixPattern, '');
    }

    // Remove stack suffix (case-insensitive, with optional separators)
    if (stackSuffix) {
      const suffixPattern = new RegExp(`[-_\\s]*${stackSuffix}$`, 'i');
      cleanName = cleanName.replace(suffixPattern, '');
    }

    // Clean up any remaining multiple spaces or separators
    cleanName = cleanName.replace(/[-_\s]+/g, ' ').trim();

    return cleanName;
  }

  /**
   * Convert PascalCase or camelCase to display name
   * @param input - The input string in PascalCase or camelCase
   * @returns Formatted display name
   */
  static pascalOrCamelToDisplayName(input: string): string {
    if (!input) return '';

    // Handle common agent type patterns
    const cleanInput = input
      .replace(/-/g, ' ') // Replace hyphens with spaces
      .trim();

    if (cleanInput.indexOf(' ') > -1) return cleanInput;
    
    // Split on capital letters and common word boundaries
    const words = cleanInput
      .replace(/([a-z])([A-Z])/g, '$1 $2') // Insert space before capital letters
      .replace(/([A-Z])([A-Z][a-z])/g, '$1 $2') // Handle consecutive capitals
      .split(/[\s_-]+/) // Split on spaces, underscores, and hyphens
      .filter(word => word.length > 0)
      .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase());

    return words.join(' ');
  }

  /**
   * Format currency values without hardcoded symbols
   * @param value - The numeric value or string to format
   * @returns Formatted number string without currency symbols
   */
  static formatCurrency(value: any): string {
    if (!value && value !== 0) return '';

    // Convert to string and remove any existing currency symbols
    const cleanValue = value.toString().replace(/[$€£¥₹]/g, '');
    
    // Parse as number
    const numericValue = parseFloat(cleanValue.replace(/[^0-9.-]/g, ''));
    
    if (isNaN(numericValue)) {
      return cleanValue;
    }

    // Format with commas for thousands
    return numericValue.toLocaleString();
  }

  /**
   * Format percentage values without hardcoded symbols
   * @param value - The numeric value or string to format
   * @returns Formatted number string without percentage symbols
   */
  static formatPercentage(value: any): string {
    if (!value && value !== 0) return '';

    // Convert to string and remove any existing percentage symbols
    const cleanValue = value.toString().replace(/%/g, '');
    
    // Parse as number
    const numericValue = parseFloat(cleanValue);
    
    if (isNaN(numericValue)) {
      return cleanValue;
    }

    // Return formatted number without % symbol
    return numericValue.toString();
  }

  /**
   * Format numeric values with appropriate precision
   * @param value - The numeric value to format
   * @param decimals - Number of decimal places (optional)
   * @returns Formatted number string
   */
  static formatNumber(value: any, decimals?: number): string {
    if (!value && value !== 0) return '';

    const numericValue = parseFloat(value.toString().replace(/[^0-9.-]/g, ''));
    
    if (isNaN(numericValue)) {
      return value.toString();
    }

    if (decimals !== undefined) {
      return numericValue.toFixed(decimals);
    }

    return numericValue.toLocaleString();
  }

  /**
   * Format budget values with K/M suffixes for large numbers
   * @param value - The numeric value to format
   * @returns Formatted budget string without currency symbols
   */
  static formatBudget(value: any): string {
    if (!value && value !== 0) return '';

    // Remove any existing currency symbols
    const cleanValue = value.toString().replace(/[$€£¥₹]/g, '');
    const numericValue = parseFloat(cleanValue.replace(/[^0-9.-]/g, ''));
    
    if (isNaN(numericValue)) {
      return cleanValue;
    }

    // Format with K/M suffixes for readability
    if (numericValue >= 1000000) {
      return `${(numericValue / 1000000).toFixed(1)}M`;
    } else if (numericValue >= 1000) {
      return `${(numericValue / 1000).toFixed(0)}K`;
    }

    return numericValue.toLocaleString();
  }

  /**
   * Format metric values based on the metric type
   * @param key - The metric key/name to determine formatting
   * @param value - The value to format
   * @returns Formatted value string
   */
  static formatMetricValue(key: string, value: any): string {
    if (!value && value !== 0) return '';

    const keyLower = key.toLowerCase();
    
    // Handle ROAS and ratio values
    if (keyLower.includes('roas') || keyLower.includes('ratio')) {
      const cleanValue = TextUtils.formatNumber(value);
      return `${cleanValue}${cleanValue.toString().includes('x') ? '' : 'x'}`;
    }
    
    // Handle percentage values
    if (keyLower.includes('rate') || keyLower.includes('cvr') || keyLower.includes('ctr') || keyLower.includes('percentage')) {
      const cleanValue = TextUtils.formatPercentage(value);
      return value.toString().includes('%') ? value : `${cleanValue}%`;
    }
    
    // Handle currency values (budget, cost, price, CPA)
    if (keyLower.includes('budget') || keyLower.includes('cost') || keyLower.includes('price') || keyLower.includes('cpa')) {
      return TextUtils.formatCurrency(value);
    }
    
    // Default number formatting
    return TextUtils.formatNumber(value);
  }

  /**
   * Extract rows from CSV-like text content
   * @param text - The CSV-like text content (comma-separated values)
   * @param headers - Optional array of header objects with name and type properties
   * @returns Array of arrays (rows of comma-separated values)
   */
  static extractRowsFromText(text: string, headers?: Array<{name: string, type: string}>): any[] {
    if (!text || !text.trim()) {
      return [];
    }

    try {
      // Split by spaces to get rows (each space-separated group is a row)
      // The content format is: "value1,value2,value3 value4,value5,value6 ..."
      let rowStrings:Array<string> = []
      if(text.indexOf('\n')==-1)
        rowStrings = text.trim().split(/\s+/);
      else rowStrings = text.split('\n')
      if (!rowStrings || rowStrings.length === 0) {
        return [];
      }

      // Convert each row string into an array of values
      const rows: any[] = [];
      
      for (const rowString of rowStrings) {
        if (!rowString.trim()) {
          continue;
        }
        
        // Split by comma to get individual values in the row
        const values = rowString.split(',').map(v => v.trim()).filter(v => v.length > 0);
        
        if (values.length > 0) {
          rows.push(values);
        }
      }
      
      return rows;
      
    } catch (error) {
      console.error('Error extracting rows from text:', error);
      return [];
    }
  }

}